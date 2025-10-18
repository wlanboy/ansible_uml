from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List
import git
import os
import tempfile
import glob
import yaml
import re

app = FastAPI()
#app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def sanitize(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[^\w\-]', '_', text)
    text = re.sub(r'__+', '_', text)
    return text

def sanitize_host(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[^\w\-]', '_', text)
    text = re.sub(r'__+', '_', text)
    if re.match(r'^\d', text):
        text = f"host_{text}"
    return text

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/scan", response_class=HTMLResponse)
async def scan_repo(request: Request, repo_input: str = Form(...)):
    if os.path.exists(repo_input):
        repo_path = os.path.abspath(repo_input)
    else:
        repo_path = tempfile.mkdtemp()
        repo = git.Repo.clone_from(repo_input, repo_path)
        try:
            repo.git.checkout("main")
        except:
            repo.git.checkout("master")

    inventories = [
        f for f in glob.glob(f"{repo_path}/**/inventory/*", recursive=True)
        if os.path.isfile(f)
    ]

    playbooks = [
        f for f in glob.glob(f"{repo_path}/**/playbooks/*.yml", recursive=True)
        if "hosts:" in open(f).read()
    ]

    print("✅ Gefundene Inventories:")
    for inv in inventories:
        print("  -", inv)

    print("✅ Gefundene Playbooks:")
    for pb in playbooks:
        print("  -", pb)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "inventories": inventories,
        "playbooks": playbooks,
        "repo_path": repo_path
    })

@app.post("/generate", response_class=HTMLResponse)
async def generate_diagram(
    request: Request,
    repo_path: str = Form(...),
    inventory: List[str] = Form(...),
    playbook: List[str] = Form(...),
    layout: str = Form("LR")
):
    mermaid = [f"graph {layout}"]
    group_nodes = set()
    host_nodes = set()
    playbook_nodes = []
    task_counter = 0

    # 1. Hostgruppen und Hosts
    for inv_path in inventory:
        with open(inv_path) as f:
            inv_data = yaml.safe_load(f)

        for group, content in inv_data.items():
            group_id = sanitize(group)
            group_nodes.add(group_id)
            mermaid.append(f'    {group_id}["Group: {group}"]')

            hosts = content.get("hosts", {})
            for host in hosts:
                host_id = sanitize(host)
                host_nodes.add(host_id)
                mermaid.append(f'    {group_id} --> {host_id}["Host: {host}"]')

    # 2. Playbooks und Tasks
    for pb_path in playbook:
        with open(pb_path) as f:
            pb_data = yaml.safe_load(f)

        pb_id = sanitize(os.path.basename(pb_path))
        playbook_nodes.append(pb_id)
        mermaid.append(f'    {pb_id}["Playbook: {os.path.basename(pb_path)}"]')

        for play in pb_data:
            group = play.get("hosts")
            if not group:
                continue
            group_id = sanitize(group)
            mermaid.append(f'    {pb_id} --> {group_id}')

            for task in play.get("tasks", []):
                task_name = task.get("name", f"task_{task_counter}")
                task_id = f"{pb_id}_task_{task_counter}"
                label = task_name.replace('"', "'")
                mermaid.append(f'    {pb_id} --> {task_id}["{label}"]')
                task_counter += 1

    diagram = "\n".join(mermaid)

    print("📊 Generiertes Mermaid-Diagramm:")
    print(diagram)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "diagram": diagram,
        "repo_path": repo_path
    })
