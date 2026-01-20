from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import git
import os
import tempfile
import shutil
import glob
import yaml
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
templates = Jinja2Templates(directory="templates")


def sanitize(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[^\w\-]', '_', text)
    text = re.sub(r'__+', '_', text)
    if re.match(r'^\d', text):
        text = f"id_{text}"
    return text


def find_role_tasks(repo_path: str, role_name: str) -> list[dict]:
    """Sucht die Tasks einer Rolle im roles/ Verzeichnis."""
    role_paths = [
        f"{repo_path}/**/roles/{role_name}/tasks/main.yml",
        f"{repo_path}/**/roles/{role_name}/tasks/main.yaml",
    ]
    for pattern in role_paths:
        for role_file in glob.glob(pattern, recursive=True):
            try:
                with open(role_file, encoding="utf-8") as f:
                    tasks = yaml.safe_load(f)
                    if tasks and isinstance(tasks, list):
                        return tasks
            except (yaml.YAMLError, IOError) as e:
                logger.warning(f"Konnte Role nicht laden: {role_file} - {e}")
    return []


def load_included_tasks(repo_path: str, task_file: str, base_path: str) -> list[dict]:
    """Lädt eingebundene Task-Dateien."""
    if not task_file:
        return []

    possible_paths = [
        os.path.join(os.path.dirname(base_path), task_file),
        os.path.join(repo_path, task_file),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    tasks = yaml.safe_load(f)
                    if tasks and isinstance(tasks, list):
                        return tasks
            except (yaml.YAMLError, IOError) as e:
                logger.warning(f"Konnte Task-Datei nicht laden: {path} - {e}")
    return []

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/scan", response_class=HTMLResponse)
async def scan_repo(request: Request, repo_input: str = Form(...)):
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
async def generate_diagram(
    request: Request,
    repo_path: str = Form(...),
    inventory: list[str] = Form(...),
    playbook: list[str] = Form(...),
    layout: str = Form("LR")
):
    mermaid = [f"graph {layout}"]
    group_nodes = set()
    host_nodes = set()
    playbook_nodes = []
    role_nodes = set()
    handler_nodes = set()
    task_counter = 0

    def process_task(task: dict, parent_id: str, prefix: str = "") -> None:
        """Verarbeitet einen einzelnen Task inkl. Roles und Includes."""
        nonlocal task_counter

        if not isinstance(task, dict):
            return

        task_name = task.get("name", f"task_{task_counter}")
        task_id = f"{parent_id}_task_{task_counter}"
        label = str(task_name).replace('"', "'")

        # include_role / import_role
        role_info = task.get("include_role") or task.get("import_role")
        if role_info:
            role_name = role_info.get("name") if isinstance(role_info, dict) else str(role_info)
            if role_name:
                role_id = sanitize(f"role_{role_name}")
                if role_id not in role_nodes:
                    role_nodes.add(role_id)
                    mermaid.append(f'    {role_id}{{"Role: {role_name}"}}')

                    # Role-Tasks laden und verarbeiten
                    role_tasks = find_role_tasks(repo_path, role_name)
                    for rt in role_tasks:
                        process_task(rt, role_id, f"{role_name}_")

                mermaid.append(f'    {parent_id} --> {role_id}')
                task_counter += 1
                return

        # include_tasks / import_tasks
        include_file = task.get("include_tasks") or task.get("import_tasks")
        if include_file:
            if isinstance(include_file, dict):
                include_file = include_file.get("file", "")
            include_id = sanitize(f"include_{os.path.basename(str(include_file))}")
            mermaid.append(f'    {parent_id} --> {include_id}[/"Include: {os.path.basename(str(include_file))}"/]')

            # Eingebundene Tasks laden
            included_tasks = load_included_tasks(repo_path, str(include_file), parent_id)
            for it in included_tasks:
                process_task(it, include_id)

            task_counter += 1
            return

        # Normaler Task
        mermaid.append(f'    {parent_id} --> {task_id}["{prefix}{label}"]')
        task_counter += 1

    try:
        # 1. Hostgruppen und Hosts
        for inv_path in inventory:
            try:
                with open(inv_path, encoding="utf-8") as f:
                    inv_data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                logger.error(f"YAML-Fehler in {inv_path}: {e}")
                return templates.TemplateResponse("index.html", {
                    "request": request,
                    "error": f"Ungültiges YAML in {inv_path}: {e}",
                    "repo_path": repo_path
                })

            if not inv_data or not isinstance(inv_data, dict):
                logger.warning(f"Leeres oder ungültiges Inventory: {inv_path}")
                continue

            for group, content in inv_data.items():
                group_id = sanitize(group)
                group_nodes.add(group_id)
                mermaid.append(f'    {group_id}["Group: {group}"]')

                if content is None or not isinstance(content, dict):
                    continue

                hosts = content.get("hosts", {})
                if hosts:
                    for host in hosts:
                        host_id = sanitize(host)
                        host_nodes.add(host_id)
                        mermaid.append(f'    {group_id} --> {host_id}["Host: {host}"]')

        # 2. Playbooks, Tasks, Roles und Handlers
        for pb_path in playbook:
            try:
                with open(pb_path, encoding="utf-8") as f:
                    pb_data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                logger.error(f"YAML-Fehler in {pb_path}: {e}")
                return templates.TemplateResponse("index.html", {
                    "request": request,
                    "error": f"Ungültiges YAML in {pb_path}: {e}",
                    "repo_path": repo_path
                })

            if not pb_data or not isinstance(pb_data, list):
                logger.warning(f"Leeres oder ungültiges Playbook: {pb_path}")
                continue

            pb_id = sanitize(os.path.basename(pb_path))
            playbook_nodes.append(pb_id)
            mermaid.append(f'    {pb_id}["Playbook: {os.path.basename(pb_path)}"]')

            for play in pb_data:
                if not isinstance(play, dict):
                    continue
                group = play.get("hosts")
                if not group:
                    continue
                group_id = sanitize(str(group))
                mermaid.append(f'    {pb_id} --> {group_id}')

                # Roles auf Play-Ebene
                for role in play.get("roles", []) or []:
                    if isinstance(role, dict):
                        role_name = role.get("role") or role.get("name")
                    else:
                        role_name = str(role)

                    if role_name:
                        role_id = sanitize(f"role_{role_name}")
                        if role_id not in role_nodes:
                            role_nodes.add(role_id)
                            mermaid.append(f'    {role_id}{{"Role: {role_name}"}}')

                            # Role-Tasks laden
                            role_tasks = find_role_tasks(repo_path, role_name)
                            for rt in role_tasks:
                                process_task(rt, role_id, f"{role_name}_")

                        mermaid.append(f'    {pb_id} --> {role_id}')

                # Tasks verarbeiten
                for task in play.get("tasks", []) or []:
                    process_task(task, pb_id)

                # Pre-Tasks
                for task in play.get("pre_tasks", []) or []:
                    process_task(task, pb_id)

                # Post-Tasks
                for task in play.get("post_tasks", []) or []:
                    process_task(task, pb_id)

                # Handlers
                for handler in play.get("handlers", []) or []:
                    if not isinstance(handler, dict):
                        continue
                    handler_name = handler.get("name", f"handler_{len(handler_nodes)}")
                    handler_id = sanitize(f"handler_{handler_name}")
                    if handler_id not in handler_nodes:
                        handler_nodes.add(handler_id)
                        label = str(handler_name).replace('"', "'")
                        mermaid.append(f'    {handler_id}(["{label}"])')
                        mermaid.append(f'    {pb_id} -.-> {handler_id}')

        diagram = "\n".join(mermaid)
        logger.info(f"Generiertes Mermaid-Diagramm mit {len(mermaid)} Zeilen")

        return templates.TemplateResponse("index.html", {
            "request": request,
            "diagram": diagram,
            "repo_path": repo_path
        })
    except Exception as e:
        logger.error(f"Fehler beim Generieren: {e}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "error": f"Fehler beim Generieren: {e}",
            "repo_path": repo_path
        })
