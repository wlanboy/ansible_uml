"""Ansible UML Visualizer - FastAPI Web Application."""

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import git
import os
import tempfile
import shutil
import glob
import logging

from ansible_parser import parse_all
from mermaid_generator import generate_diagram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Startseite anzeigen."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/scan", response_class=HTMLResponse)
async def scan_repo(request: Request, repo_input: str = Form(...)):
    """Repository scannen und Inventories/Playbooks finden."""
    temp_dir = None
    try:
        if os.path.exists(repo_input):
            repo_path = os.path.abspath(repo_input)
        else:
            temp_dir = tempfile.mkdtemp()
            repo_path = temp_dir
            repo = git.Repo.clone_from(repo_input, repo_path)
            try:
                repo.git.checkout("main")
            except git.exc.GitCommandError:
                repo.git.checkout("master")

        inventories = [
            f for f in glob.glob(f"{repo_path}/**/inventory/*", recursive=True)
            if os.path.isfile(f)
        ]

        playbooks = []
        for f in glob.glob(f"{repo_path}/**/playbooks/*.yml", recursive=True):
            try:
                with open(f, encoding="utf-8") as file:
                    if "hosts:" in file.read():
                        playbooks.append(f)
            except (IOError, UnicodeDecodeError) as e:
                logger.warning(f"Konnte Datei nicht lesen: {f} - {e}")

        logger.info(f"Gefundene Inventories: {inventories}")
        logger.info(f"Gefundene Playbooks: {playbooks}")

        return templates.TemplateResponse("index.html", {
            "request": request,
            "inventories": inventories,
            "playbooks": playbooks,
            "repo_path": repo_path
        })
    except git.exc.GitCommandError as e:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        logger.error(f"Git-Fehler: {e}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "error": f"Git-Fehler: {e}"
        })
    except Exception as e:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        logger.error(f"Unerwarteter Fehler: {e}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "error": f"Fehler beim Scannen: {e}"
        })


@app.post("/generate", response_class=HTMLResponse)
async def generate_diagram_route(
    request: Request,
    repo_path: str = Form(...),
    inventory: list[str] = Form(...),
    playbook: list[str] = Form(...),
    layout: str = Form("LR")
):
    """Mermaid-Diagramm generieren."""
    try:
        # Ansible-Daten parsen
        ansible_data = parse_all(inventory, playbook, repo_path)

        # Mermaid-Diagramm generieren
        diagram = generate_diagram(ansible_data, layout, repo_path)

        logger.info(f"Generiertes Mermaid-Diagramm mit {len(diagram.splitlines())} Zeilen")

        return templates.TemplateResponse("index.html", {
            "request": request,
            "diagram": diagram,
            "repo_path": repo_path,
            "layout": layout
        })
    except ValueError as e:
        logger.error(f"Parsing-Fehler: {e}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "error": str(e),
            "repo_path": repo_path
        })
    except Exception as e:
        logger.error(f"Fehler beim Generieren: {e}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "error": f"Fehler beim Generieren: {e}",
            "repo_path": repo_path
        })
