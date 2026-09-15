const express = require('express');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT || 3000;

const FLAG_SECRET = process.env.FLAG_SECRET || 'vbank_ctf_default_2024';
const TEAM_ID     = process.env.TEAM_ID     || '0';

function makeFlag(challenge) {
  const digest = crypto.createHmac('sha256', FLAG_SECRET)
    .update(`${TEAM_ID}:${challenge}`)
    .digest('hex')
    .slice(0, 24);
  return `LYD{${digest}}`;
}

const FLAG = makeFlag('proto_pollution');

app.use(express.json({ limit: '256kb' }));

const defaultSettings = () => ({
  service: 'VBank Analytics',
  version: '2.3.7',
  theme: 'light',
  lang: 'en',
  widgets: {
    summary: true,
    trends: true
  }
});

let settings = defaultSettings();

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function deepMerge(target, source) {
  for (const key in source) {
    const value = source[key];
    if (isObject(value)) {
      if (!isObject(target[key])) {
        target[key] = {};
      }
      deepMerge(target[key], value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

function resetPrototypePollution() {
  delete Object.prototype.isAdmin;
}

app.get('/', (_req, res) => {
  res.json({
    service: 'VBank Analytics',
    status: 'ok',
    endpoints: [
      'GET /health',
      'GET /api/config/current',
      'POST /api/config/merge',
      'GET /api/config/admin-panel',
      'POST /api/config/reset'
    ]
  });
});

app.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    service: 'vbank-analytics',
    polluted: settings.isAdmin === true
  });
});

app.get('/api/config/current', (_req, res) => {
  res.json({
    success: true,
    settings,
    note: 'Settings are merged recursively.'
  });
});

app.post('/api/config/merge', (req, res) => {
  if (!isObject(req.body)) {
    return res.status(400).json({ error: 'Expected a JSON object payload' });
  }

  deepMerge(settings, req.body);

  return res.json({
    success: true,
    settings,
    inheritedAdmin: settings.isAdmin === true,
    note: 'Merged configuration applied'
  });
});

app.get('/api/config/admin-panel', (_req, res) => {
  if (settings.isAdmin === true) {
    return res.json({
      access: 'granted',
      message: 'Prototype pollution successful. Analytics admin unlocked.',
      flag: FLAG
    });
  }

  return res.status(403).json({
    access: 'denied',
    message: 'Analytics admin privileges are disabled.',
    current_isAdmin: settings.isAdmin || false
  });
});

app.post('/api/config/reset', (_req, res) => {
  resetPrototypePollution();
  settings = defaultSettings();
  res.json({
    success: true,
    settings
  });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`vbank-analytics listening on port ${PORT}`);
});
