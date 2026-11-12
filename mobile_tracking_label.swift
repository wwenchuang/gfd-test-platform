import AppKit
import Vision

let image = URL(fileURLWithPath: CommandLine.arguments[1])
let label = CommandLine.arguments[2]
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
try VNImageRequestHandler(url: image).perform([request])
let matches = (request.results ?? []).filter { observation in
    observation.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines) == label
}
if matches.count == 1, let picture = NSBitmapImageRep(data: try Data(contentsOf: image)) {
    let box = matches[0].boundingBox
    print(Int(box.midX * Double(picture.pixelsWide)), Int((1-box.midY) * Double(picture.pixelsHigh)))
}
