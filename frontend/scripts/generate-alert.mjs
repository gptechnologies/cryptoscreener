// Generate a tiny two-note PCM WAV asset without a runtime audio dependency.
import { writeFileSync } from 'node:fs'

const destination = process.argv[2]
if (!destination) throw new Error('Usage: node scripts/generate-alert.mjs output.wav')
const sampleRate = 22050
const seconds = 0.38
const samples = Math.ceil(sampleRate * seconds)
const output = Buffer.alloc(44 + samples * 2)
output.write('RIFF', 0)
output.writeUInt32LE(output.length - 8, 4)
output.write('WAVEfmt ', 8)
output.writeUInt32LE(16, 16)
output.writeUInt16LE(1, 20)
output.writeUInt16LE(1, 22)
output.writeUInt32LE(sampleRate, 24)
output.writeUInt32LE(sampleRate * 2, 28)
output.writeUInt16LE(2, 32)
output.writeUInt16LE(16, 34)
output.write('data', 36)
output.writeUInt32LE(samples * 2, 40)
for (let i = 0; i < samples; i++) {
  const time = i / sampleRate
  const tone = time < 0.15 ? 660 : 880
  const noteTime = time < 0.15 ? time : time - 0.15
  const noteLength = time < 0.15 ? 0.15 : seconds - 0.15
  const envelope = Math.min(1, noteTime / 0.012) * Math.max(0, 1 - noteTime / noteLength)
  output.writeInt16LE(Math.round(Math.sin(2 * Math.PI * tone * time) * envelope * 7500), 44 + i * 2)
}
writeFileSync(destination, output)
