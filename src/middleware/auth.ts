import { Request, Response, NextFunction } from 'express';
import { v4 as uuidv4 } from 'uuid';

export function requireApiKey(req: Request, res: Response, next: NextFunction): void {
  const serviceApiKey = process.env.SERVICE_API_KEY;

  if (!serviceApiKey) {
    res.status(500).json({
      ok: false,
      error: { code: 'MISCONFIGURATION', message: 'Service API key not configured.' },
      traceId: uuidv4(),
    });
    return;
  }

  const provided = req.headers['x-api-key'];

  if (!provided || provided !== serviceApiKey) {
    res.status(401).json({
      ok: false,
      error: { code: 'UNAUTHORIZED', message: 'Missing or invalid X-API-Key header.' },
      traceId: uuidv4(),
    });
    return;
  }

  next();
}
