import path from 'path';
import os from 'os';
import fs from 'fs';
import { v4 as uuidv4 } from 'uuid';
import youtubedl from 'youtube-dl-exec';
import { GoogleGenerativeAI, GoogleAIFileManager, FileState } from '@google/generative-ai';

const DEFAULT_PROMPT =
  'Describe in detail what happens in this video, including visuals and audio.';

const DEFAULT_MODEL = 'gemini-2.0-flash';
const POLL_INTERVAL_MS = 5_000;
const MAX_POLL_ATTEMPTS = 24; // 2 minutes total

export interface AnalysisResult {
  analysis: string;
  model: string;
}

export class AppError extends Error {
  constructor(
    public readonly code: string,
    message: string
  ) {
    super(message);
    this.name = 'AppError';
  }
}

export async function analyzeVideo(
  url: string,
  prompt: string = DEFAULT_PROMPT
): Promise<AnalysisResult> {
  const geminiApiKey = process.env.GEMINI_API_KEY;
  if (!geminiApiKey) {
    throw new AppError('MISCONFIGURATION', 'GEMINI_API_KEY is not set.');
  }

  const model = process.env.GEMINI_MODEL ?? DEFAULT_MODEL;
  const tmpFile = path.join(os.tmpdir(), `video-${uuidv4()}.mp4`);

  try {
    // Step 1: Download video via yt-dlp (platform-agnostic)
    await downloadVideo(url, tmpFile);

    // Step 2: Upload to Gemini File API
    const fileManager = new GoogleAIFileManager(geminiApiKey);
    const uploadResponse = await fileManager.uploadFile(tmpFile, {
      mimeType: 'video/mp4',
      displayName: path.basename(tmpFile),
    });

    // Step 3: Poll until file state is ACTIVE
    let fileResource = uploadResponse.file;
    let attempts = 0;

    while (fileResource.state === FileState.PROCESSING) {
      if (attempts >= MAX_POLL_ATTEMPTS) {
        throw new AppError(
          'GEMINI_TIMEOUT',
          'Gemini file processing timed out after 2 minutes.'
        );
      }
      await sleep(POLL_INTERVAL_MS);
      fileResource = await fileManager.getFile(fileResource.name);
      attempts++;
    }

    if (fileResource.state === FileState.FAILED) {
      throw new AppError('GEMINI_FILE_FAILED', 'Gemini file processing failed.');
    }

    // Step 4: Generate content
    const genAI = new GoogleGenerativeAI(geminiApiKey);
    const generativeModel = genAI.getGenerativeModel({ model });

    const result = await generativeModel.generateContent([
      { fileData: { mimeType: 'video/mp4', fileUri: fileResource.uri } },
      { text: prompt },
    ]);

    return { analysis: result.response.text(), model };
  } finally {
    // Always clean up temp file
    try {
      if (fs.existsSync(tmpFile)) {
        fs.unlinkSync(tmpFile);
      }
    } catch {
      console.warn(`[cleanup] Failed to delete temp file: ${tmpFile}`);
    }
  }
}

async function downloadVideo(url: string, outputPath: string): Promise<void> {
  try {
    await youtubedl(url, {
      output: outputPath,
      mergeOutputFormat: 'mp4',
      maxFilesize: '500m',
      // Prefer mp4 video + m4a audio for clean ffmpeg merge; fallback to best available
      format: 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
      // Helps bypass bot detection on TikTok, Instagram, etc.
      addHeader: ['referer:youtube.com', 'user-agent:googlebot'],
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    throw new AppError('DOWNLOAD_FAILED', `Failed to download video: ${message}`);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
