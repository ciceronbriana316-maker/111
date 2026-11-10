# Clip Finder

Paste a YouTube link, get the best clips ranked, preview them, and save the ones you want.

## One-time setup

1. Install Python 3.10+ (python.org) and FFmpeg (needed only for saving clips).
   - Mac: `brew install ffmpeg`
   - Windows: `winget install ffmpeg`
2. Open a terminal in this folder and run:

       python -m venv venv
       source venv/bin/activate        # Windows: venv\Scripts\activate
       pip install -r requirements.txt

3. Get an API key at console.anthropic.com and set it:

       export ANTHROPIC_API_KEY="your-key"     # Windows: setx ANTHROPIC_API_KEY "your-key" (then reopen the terminal)

## Run it

    python app.py

Open http://localhost:5055, paste a link, click "Find clips".

Saved clips land in the `clips/` folder.

## Notes

- Finding clips only needs the video's captions. If a video has captions turned off, you'll get an error.
- "Make clip" downloads just that section with yt-dlp, crops it to vertical 9:16 (centered) and burns in captions from the video's own transcript. Both are optional under Options. Use it on content you own or have permission to use.
- To use a different Claude model: `export CLIPFINDER_MODEL="claude-opus-5-5"`.
- If transcript fetching fails after working before, run `pip install -U youtube-transcript-api yt-dlp`. YouTube changes often.

## Hosting online

Deploy with the included Dockerfile (Render, Railway, Fly.io all work).

Environment variables to set on the host:

- `ANTHROPIC_API_KEY`: your key (required)
- `APP_PASSWORD`: a password the site asks for before it does anything (required; without it anyone can spend your API credit)
- `PROXY_URL`: optional, e.g. `http://user:pass@host:port`. YouTube often blocks cloud servers, and a residential proxy fixes that.

Saved clips are stored on the server's disk, which most hosts wipe on redeploy, so download them right away.
