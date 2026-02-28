import 'dotenv/config';
import express, { Request, Response, NextFunction } from 'express';
import { v4 as uuidv4 } from 'uuid';
import { requireApiKey } from './middleware/auth';
import { analyzeVideo, AppError } from './analyze';

const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json({ limit: '1mb' }));

// Health check — no auth required
app.get('/health', (_req, res) => {
  res.json({ ok: true, data: { status: 'healthy' } });
});

// Main analysis endpoint
app.post('/api/analyze', requireApiKey, async (req: Request, res: Response) => {
  const { url, prompt } = req.body as { url?: unknown; prompt?: unknown };

  if (!url || typeof url !== 'string') {
    res.status(400).json({
      ok: false,
      error: { code: 'INVALID_INPUT', message: '"url" must be a non-empty string.' },
      traceId: uuidv4(),
    });
    return;
  }

  if (prompt !== undefined && typeof prompt !== 'string') {
    res.status(400).json({
      ok: false,
      error: { code: 'INVALID_INPUT', message: '"prompt" must be a string if provided.' },
      traceId: uuidv4(),
    });
    return;
  }

  try {
    const result = await analyzeVideo(url, prompt ?? undefined);
    res.json({ ok: true, data: result });
  } catch (err: unknown) {
    const traceId = uuidv4();
    console.error(`[${traceId}] Analysis error:`, err);

    if (err instanceof AppError) {
      const statusMap: Record<string, number> = {
        INVALID_INPUT: 400,
        UNAUTHORIZED: 401,
        DOWNLOAD_FAILED: 422,
        GEMINI_TIMEOUT: 504,
        GEMINI_FILE_FAILED: 502,
        MISCONFIGURATION: 500,
      };
      const status = statusMap[err.code] ?? 500;
      res.status(status).json({
        ok: false,
        error: { code: err.code, message: err.message },
        traceId,
      });
      return;
    }

    res.status(500).json({
      ok: false,
      error: { code: 'INTERNAL_ERROR', message: 'An unexpected error occurred.' },
      traceId,
    });
  }
});

// 404 fallback
app.use((_req, res) => {
  res.status(404).json({
    ok: false,
    error: { code: 'NOT_FOUND', message: 'Endpoint not found.' },
    traceId: uuidv4(),
  });
});

// Global error handler — all 4 params required by Express
// eslint-disable-next-line @typescript-eslint/no-unused-vars
app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  const traceId = uuidv4();
  console.error(`[${traceId}] Unhandled middleware error:`, err);
  res.status(500).json({
    ok: false,
    error: { code: 'INTERNAL_ERROR', message: 'An unexpected error occurred.' },
    traceId,
  });
});

app.listen(PORT, () => {
  console.log(`[server] Listening on port ${PORT}`);
});
