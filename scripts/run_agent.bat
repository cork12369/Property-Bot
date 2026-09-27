@echo off
REM PropertyBot nightly agent run: scrape listings, score with LLM, deliver top picks.
cd /d "%~dp0.."
python main.py scrape --max-results 20 --headless
if errorlevel 1 (
    echo [run_agent] Scrape step failed; still attempting agent run on existing data.
)
python main.py agent run --top-n 5
