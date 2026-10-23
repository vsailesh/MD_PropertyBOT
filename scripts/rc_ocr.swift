// OCR top strip of a screenshot: prints recognized text (one line joined).
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count > 1,
      let img = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    print(""); exit(0)
}
let H = CGFloat(cg.height)
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
// instruction band: top 240px of an 800x1160 bframe shot
req.regionOfInterest = CGRect(x: 0, y: 1 - 240 / H, width: 1, height: 240 / H)
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try? handler.perform([req])
var out: [String] = []
for obs in req.results ?? [] {
    if let s = obs.topCandidates(1).first?.string { out.append(s) }
}
print(out.joined(separator: " "))
