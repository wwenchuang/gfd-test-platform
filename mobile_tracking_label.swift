import AppKit
import Vision

let image = URL(fileURLWithPath: CommandLine.arguments[1])
let label = CommandLine.arguments[2]
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
try VNImageRequestHandler(url: image).perform([request])
var boxes: [CGRect] = []
for observation in request.results ?? [] {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let text = candidate.string
    for token in text.split(whereSeparator: { $0.isWhitespace }) where token == label {
        if token.count == text.count {
            boxes.append(observation.boundingBox)
        } else if let word = try candidate.boundingBox(for: token.startIndex..<token.endIndex),
                  word.boundingBox.width < observation.boundingBox.width * 0.9 {
            boxes.append(word.boundingBox)
        }
    }
}
if boxes.count == 1, let picture = NSBitmapImageRep(data: try Data(contentsOf: image)) {
    let box = boxes[0]
    print(Int(box.midX * Double(picture.pixelsWide)), Int((1-box.midY) * Double(picture.pixelsHigh)))
}
