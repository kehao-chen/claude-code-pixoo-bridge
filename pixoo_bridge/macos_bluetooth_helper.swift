import Foundation
import IOBluetooth

struct SendRequest: Decodable {
    let channel_id: Int
    let device_mac: String
    let packets: [String]
    let packet_gap_ms: Int?
    let settle_ms: Int?
}

struct HelperResponse: Encodable {
    let ok: Bool
    let bundle_identifier: String?
    let bundle_path: String?
    let bytes_sent: Int?
    let device_channel: Int?
    let device_mac: String?
    let error: String?
    let has_bluetooth_usage_description: Bool?
    let packet_count: Int?
}

struct CommandLineOptions {
    let bundleInfo: Bool
    let requestFile: String?
    let responseFile: String?
}

enum HelperError: LocalizedError {
    case invalidRequest(String)
    case bluetooth(String)
    case io(String)

    var errorDescription: String? {
        switch self {
        case .invalidRequest(let message), .bluetooth(let message), .io(let message):
            return message
        }
    }
}

final class RFCOMMDelegate: NSObject {}

extension Data {
    init(hexEncoded value: String) throws {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard normalized.count.isMultiple(of: 2) else {
            throw HelperError.invalidRequest("packet hex payload must have an even length")
        }

        self.init(capacity: normalized.count / 2)
        var currentIndex = normalized.startIndex
        while currentIndex < normalized.endIndex {
            let nextIndex = normalized.index(currentIndex, offsetBy: 2)
            let byteString = normalized[currentIndex..<nextIndex]
            guard let byte = UInt8(byteString, radix: 16) else {
                throw HelperError.invalidRequest("packet payload must be hexadecimal")
            }
            append(byte)
            currentIndex = nextIndex
        }
    }
}

func parseArguments(_ arguments: ArraySlice<String>) throws -> CommandLineOptions {
    var bundleInfo = false
    var requestFile: String?
    var responseFile: String?

    var iterator = arguments.makeIterator()
    while let argument = iterator.next() {
        if argument.hasPrefix("-psn_") {
            continue
        }
        switch argument {
        case "--bundle-info":
            bundleInfo = true
        case "--request-file":
            guard let value = iterator.next() else {
                throw HelperError.invalidRequest("--request-file requires a path")
            }
            requestFile = value
        case "--response-file":
            guard let value = iterator.next() else {
                throw HelperError.invalidRequest("--response-file requires a path")
            }
            responseFile = value
        default:
            throw HelperError.invalidRequest("unknown argument: \(argument)")
        }
    }

    return CommandLineOptions(
        bundleInfo: bundleInfo,
        requestFile: requestFile,
        responseFile: responseFile
    )
}

func emit(_ response: HelperResponse, to responseFile: String?) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let responseData = try encoder.encode(response)

    if let responseFile {
        let url = URL(fileURLWithPath: responseFile)
        try responseData.write(to: url)
        return
    }

    FileHandle.standardOutput.write(responseData)
    FileHandle.standardOutput.write(Data([0x0A]))
}

func bundleInfoResponse() -> HelperResponse {
    let bundle = Bundle.main
    let usageDescription =
        bundle.object(forInfoDictionaryKey: "NSBluetoothAlwaysUsageDescription")
        ?? bundle.object(forInfoDictionaryKey: "NSBluetoothPeripheralUsageDescription")
    return HelperResponse(
        ok: true,
        bundle_identifier: bundle.bundleIdentifier,
        bundle_path: bundle.bundlePath,
        bytes_sent: nil,
        device_channel: nil,
        device_mac: nil,
        error: nil,
        has_bluetooth_usage_description: usageDescription != nil,
        packet_count: nil
    )
}

func normalizeMacAddress(_ value: String) throws -> String {
    let normalized = value
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .replacingOccurrences(of: ":", with: "")
        .replacingOccurrences(of: "-", with: "")
        .uppercased()
    guard normalized.count == 12 else {
        throw HelperError.invalidRequest(
            "device MAC must contain exactly 12 hex characters"
        )
    }
    guard normalized.range(of: "^[0-9A-F]{12}$", options: .regularExpression) != nil else {
        throw HelperError.invalidRequest("device MAC must be hexadecimal")
    }

    var groups: [String] = []
    var index = normalized.startIndex
    while index < normalized.endIndex {
        let nextIndex = normalized.index(index, offsetBy: 2)
        groups.append(String(normalized[index..<nextIndex]))
        index = nextIndex
    }
    return groups.joined(separator: ":")
}

func formatIOReturn(_ status: IOReturn) -> String {
    String(format: "%d (0x%08X)", status, UInt32(bitPattern: status))
}

func sleepMilliseconds(_ milliseconds: Int) {
    guard milliseconds > 0 else { return }
    Thread.sleep(forTimeInterval: Double(milliseconds) / 1000.0)
}

func decodeRequest(from requestFile: String?) throws -> SendRequest {
    let inputData: Data
    if let requestFile {
        let url = URL(fileURLWithPath: requestFile)
        do {
            inputData = try Data(contentsOf: url)
        } catch {
            throw HelperError.io("failed reading request file: \(requestFile)")
        }
    } else {
        inputData = FileHandle.standardInput.readDataToEndOfFile()
    }

    guard !inputData.isEmpty else {
        throw HelperError.invalidRequest("expected JSON request input")
    }
    return try JSONDecoder().decode(SendRequest.self, from: inputData)
}

func sendPackets(request: SendRequest) throws -> HelperResponse {
    guard request.channel_id > 0 && request.channel_id < 256 else {
        throw HelperError.invalidRequest("RFCOMM channel must be between 1 and 255")
    }

    let deviceMac = try normalizeMacAddress(request.device_mac)
    let addressString = deviceMac.replacingOccurrences(of: ":", with: "-")
    guard let device = IOBluetoothDevice(addressString: addressString) else {
        throw HelperError.bluetooth(
            "failed creating Bluetooth device handle for \(deviceMac)"
        )
    }
    defer { _ = device.closeConnection() }

    let delegate = RFCOMMDelegate()
    var channel: IOBluetoothRFCOMMChannel?
    let openStatus = device.openRFCOMMChannelSync(
        &channel,
        withChannelID: BluetoothRFCOMMChannelID(request.channel_id),
        delegate: delegate
    )
    guard openStatus == kIOReturnSuccess else {
        throw HelperError.bluetooth(
            "failed opening RFCOMM channel \(request.channel_id) for \(deviceMac): "
                + "IOReturn \(formatIOReturn(openStatus))"
        )
    }
    guard let channel else {
        throw HelperError.bluetooth(
            "failed opening RFCOMM channel \(request.channel_id) for \(deviceMac): "
                + "IOBluetooth returned no channel"
        )
    }
    defer { _ = channel.close() }

    var bytesSent = 0
    let packetGapMs = max(request.packet_gap_ms ?? 30, 0)
    let settleMs = max(request.settle_ms ?? 500, 0)

    for (index, packetHex) in request.packets.enumerated() {
        let packetData = try Data(hexEncoded: packetHex)
        guard packetData.count <= Int(UInt16.max) else {
            throw HelperError.invalidRequest(
                "Pixoo packet exceeds RFCOMM writeSync length limit"
            )
        }
        if packetData.isEmpty {
            continue
        }
        let writeStatus = packetData.withUnsafeBytes { buffer -> IOReturn in
            channel.writeSync(
                UnsafeMutableRawPointer(mutating: buffer.baseAddress),
                length: UInt16(packetData.count)
            )
        }
        guard writeStatus == kIOReturnSuccess else {
            throw HelperError.bluetooth(
                "failed writing Pixoo packet over RFCOMM: "
                    + "IOReturn \(formatIOReturn(writeStatus))"
            )
        }
        bytesSent += packetData.count

        if index + 1 < request.packets.count {
            sleepMilliseconds(packetGapMs)
        }
    }

    sleepMilliseconds(settleMs)

    return HelperResponse(
        ok: true,
        bundle_identifier: nil,
        bundle_path: nil,
        bytes_sent: bytesSent,
        device_channel: request.channel_id,
        device_mac: deviceMac,
        error: nil,
        has_bluetooth_usage_description: nil,
        packet_count: request.packets.count
    )
}

var responseFile: String?

do {
    let options = try parseArguments(CommandLine.arguments.dropFirst())
    responseFile = options.responseFile

    if options.bundleInfo {
        try emit(bundleInfoResponse(), to: responseFile)
        exit(EXIT_SUCCESS)
    }

    let request = try decodeRequest(from: options.requestFile)
    try emit(try sendPackets(request: request), to: responseFile)
    exit(EXIT_SUCCESS)
} catch {
    let message = error.localizedDescription
    let errorResponse = HelperResponse(
        ok: false,
        bundle_identifier: nil,
        bundle_path: nil,
        bytes_sent: nil,
        device_channel: nil,
        device_mac: nil,
        error: message,
        has_bluetooth_usage_description: nil,
        packet_count: nil
    )
    do {
        try emit(errorResponse, to: responseFile)
    } catch {
        let fallback = "PixooBluetoothHelper failed to emit error response: \(message)\n"
        FileHandle.standardError.write(Data(fallback.utf8))
    }
    exit(EXIT_FAILURE)
}
