# MediaVault Deployment Guide

## Quick Deploy Options

### 🚀 **Railway** (Recommended - Simplest)
1. Go to [Railway.app](https://railway.app)
2. Click **"New Project"**
3. Select **"Deploy from GitHub"**
4. Connect your GitHub account & select `anantbrook/mediavault`
5. Railway auto-detects `Procfile` and deploys automatically
6. Your app is live at `https://mediavault-[random].railway.app`

**Environment Variables to add:**
```
FLASK_ENV=production
API_KEY=your_api_key_here
DATABASE_URL=your_db_url
```

---

### 🎯 **Render** (Good Alternative)
1. Go to [Render.com](https://render.com)
2. Click **"New +"** → **"Web Service"**
3. Connect GitHub repo `anantbrook/mediavault`
4. Select branch `main`
5. Settings auto-populate from `render.yaml`
6. Click **"Create Web Service"**
7. App deploys to `https://mediavault-[auto].onrender.com`

**Auto-configured via `render.yaml`** ✅

---

### ⚡ **Koyeb** (Fastest Startup)
1. Go to [Koyeb.com](https://koyeb.com)
2. Click **"Create Service"** → **"GitHub"**
3. Authorize & select `anantbrook/mediavault`
4. Builder detects `koyeb.yaml` automatically
5. Deploy with one click
6. App runs at `https://mediavault-[random].koyeb.app`

---

## Local Development

### Prerequisites
```bash
# Install Python 3.8+
python --version

# Install Docker (optional, for containerized setup)
docker --version
```

### Setup
```bash
# Clone repository
git clone https://github.com/anantbrook/mediavault.git
cd mediavault

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cat > .env << EOF
FLASK_ENV=development
API_KEY=dev_key_123
DATABASE_URL=sqlite:///mediavault.db
EOF

# Run application
python app.py
```

App runs at `http://localhost:5000`

---

## Docker Deployment

### Build & Run Locally
```bash
# Build image
docker build -t mediavault:latest .

# Run container
docker run -p 5000:5000 mediavault:latest

# Or use docker-compose with Redis + SQLite
docker-compose up -d
```

### Deploy to Docker Hub
```bash
docker tag mediavault:latest anantbrook/mediavault:latest
docker push anantbrook/mediavault:latest
```

---

## Production Checklist

- [ ] Set `FLASK_ENV=production`
- [ ] Enable HTTPS (all platforms provide free SSL)
- [ ] Set strong `API_KEY` in environment variables
- [ ] Configure database (PostgreSQL recommended for production)
- [ ] Set up monitoring & logging
- [ ] Enable rate limiting on API endpoints
- [ ] Backup uploaded media files regularly
- [ ] Monitor server logs for errors

---

## Troubleshooting

### App won't start
```bash
# Check logs on Railway/Render/Koyeb dashboard
# Or locally:
python app.py --debug
```

### Database connection error
```bash
# Verify DATABASE_URL environment variable is set
# For SQLite: sqlite:///mediavault.db
# For PostgreSQL: postgresql://user:pass@host/db
```

### Import errors
```bash
pip install --upgrade -r requirements.txt
```

---

## Support

- **Railway Docs**: https://docs.railway.app
- **Render Docs**: https://render.com/docs
- **Koyeb Docs**: https://docs.koyeb.com
- **GitHub Issues**: [Report bugs](https://github.com/anantbrook/mediavault/issues)