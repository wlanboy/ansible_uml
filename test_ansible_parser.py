"""Tests für ansible_parser.py."""

import os
import tempfile
import pytest
import yaml

from ansible_parser import (
    AnsibleData,
    _collect_roles_from_tasks,
    _parse_yaml_group,
    extract_task_info,
    find_role_dependencies,
    find_role_tasks,
    load_included_tasks,
    parse_all,
    parse_ini_inventory,
    parse_inventory,
    parse_playbook,
)


@pytest.fixture
def tmp_dir():
    """Erstellt ein temporäres Verzeichnis und räumt es danach auf."""
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d)


def _write_yaml(path, data):
    """Hilfsfunktion: Schreibt YAML-Daten in eine Datei."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def _write_text(path, text):
    """Hilfsfunktion: Schreibt Text in eine Datei."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ============================================================
# _parse_yaml_group
# ============================================================

class TestParseYamlGroup:
    def test_flat_hosts(self):
        groups = {}
        content = {"hosts": {"web1": {}, "web2": {}}}
        _parse_yaml_group(groups, "webservers", content)
        assert groups == {"webservers": ["web1", "web2"]}

    def test_children_recursive(self):
        groups = {}
        content = {
            "children": {
                "webservers": {
                    "hosts": {"web1": {}}
                },
                "databases": {
                    "children": {
                        "mysql": {
                            "hosts": {"db1": {}}
                        }
                    }
                }
            }
        }
        _parse_yaml_group(groups, "all", content)
        assert groups["all"] == []
        assert groups["webservers"] == ["web1"]
        assert groups["databases"] == []
        assert groups["mysql"] == ["db1"]

    def test_none_content(self):
        groups = {}
        _parse_yaml_group(groups, "empty", None)
        assert groups == {"empty": []}

    def test_non_dict_content(self):
        groups = {}
        _parse_yaml_group(groups, "broken", "not a dict")
        assert groups == {"broken": []}

    def test_hosts_and_children_combined(self):
        groups = {}
        content = {
            "hosts": {"parent_host": {}},
            "children": {
                "child_group": {
                    "hosts": {"child_host": {}}
                }
            }
        }
        _parse_yaml_group(groups, "parent", content)
        assert groups["parent"] == ["parent_host"]
        assert groups["child_group"] == ["child_host"]


# ============================================================
# parse_ini_inventory
# ============================================================

class TestParseIniInventory:
    def test_simple_groups(self, tmp_dir):
        ini = "[webservers]\nweb1.example.com\nweb2.example.com\n\n[databases]\ndb1.example.com\n"
        path = os.path.join(tmp_dir, "hosts")
        _write_text(path, ini)
        groups = parse_ini_inventory(path)
        assert groups["webservers"] == ["web1.example.com", "web2.example.com"]
        assert groups["databases"] == ["db1.example.com"]

    def test_host_with_variables(self, tmp_dir):
        ini = "[webservers]\nweb1 ansible_port=2222 ansible_host=10.0.0.1\n"
        path = os.path.join(tmp_dir, "hosts")
        _write_text(path, ini)
        groups = parse_ini_inventory(path)
        assert groups["webservers"] == ["web1"]

    def test_children_section(self, tmp_dir):
        ini = (
            "[webservers]\nweb1\n\n"
            "[databases]\ndb1\n\n"
            "[production:children]\nwebservers\ndatabases\n"
        )
        path = os.path.join(tmp_dir, "hosts")
        _write_text(path, ini)
        groups = parse_ini_inventory(path)
        assert groups["production"] == []
        assert groups["webservers"] == ["web1"]
        assert groups["databases"] == ["db1"]

    def test_vars_section_ignored(self, tmp_dir):
        ini = "[webservers]\nweb1\n\n[webservers:vars]\nhttp_port=80\n"
        path = os.path.join(tmp_dir, "hosts")
        _write_text(path, ini)
        groups = parse_ini_inventory(path)
        assert groups["webservers"] == ["web1"]

    def test_comments_and_empty_lines(self, tmp_dir):
        ini = "# Kommentar\n; Auch ein Kommentar\n\n[webservers]\n# Kommentar\nweb1\n\n"
        path = os.path.join(tmp_dir, "hosts")
        _write_text(path, ini)
        groups = parse_ini_inventory(path)
        assert groups["webservers"] == ["web1"]

    def test_empty_group(self, tmp_dir):
        ini = "[empty_group]\n\n[webservers]\nweb1\n"
        path = os.path.join(tmp_dir, "hosts")
        _write_text(path, ini)
        groups = parse_ini_inventory(path)
        assert groups["empty_group"] == []
        assert groups["webservers"] == ["web1"]


# ============================================================
# parse_inventory (YAML + INI dispatch)
# ============================================================

class TestParseInventory:
    def test_yaml_simple(self, tmp_dir):
        inv = {"webservers": {"hosts": {"web1": {}, "web2": {}}}}
        path = os.path.join(tmp_dir, "inventory.yml")
        _write_yaml(path, inv)
        groups = parse_inventory(path)
        assert set(groups["webservers"]) == {"web1", "web2"}

    def test_yaml_with_children(self, tmp_dir):
        inv = {
            "all": {
                "children": {
                    "webservers": {"hosts": {"web1": {}}},
                    "databases": {"hosts": {"db1": {}}}
                }
            }
        }
        path = os.path.join(tmp_dir, "inventory.yml")
        _write_yaml(path, inv)
        groups = parse_inventory(path)
        assert groups["all"] == []
        assert groups["webservers"] == ["web1"]
        assert groups["databases"] == ["db1"]

    def test_ini_fallback(self, tmp_dir):
        ini = "[webservers]\nweb1\nweb2\n"
        path = os.path.join(tmp_dir, "hosts.ini")
        _write_text(path, ini)
        groups = parse_inventory(path)
        assert groups["webservers"] == ["web1", "web2"]

    def test_yaml_empty_inventory(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.yml")
        _write_text(path, "")
        groups = parse_inventory(path)
        assert groups == {}

    def test_yaml_group_without_hosts(self, tmp_dir):
        inv = {"webservers": {"vars": {"http_port": 80}}}
        path = os.path.join(tmp_dir, "inventory.yml")
        _write_yaml(path, inv)
        groups = parse_inventory(path)
        assert groups["webservers"] == []


# ============================================================
# extract_task_info
# ============================================================

class TestExtractTaskInfo:
    def test_simple_task(self):
        task = {"name": "Install nginx", "apt": {"name": "nginx"}}
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["name"] == "Install nginx"
        assert info["type"] == "task"

    def test_unnamed_task(self):
        task = {"debug": {"msg": "hello"}}
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["name"] == "unnamed_task"

    def test_notify_string(self):
        task = {"name": "Install", "apt": {"name": "nginx"}, "notify": "Restart nginx"}
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["notify"] == ["Restart nginx"]

    def test_notify_list(self):
        task = {"name": "Install", "apt": {"name": "nginx"}, "notify": ["Restart nginx", "Reload config"]}
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["notify"] == ["Restart nginx", "Reload config"]

    def test_no_notify(self):
        task = {"name": "Simple task", "debug": {"msg": "hi"}}
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert "notify" not in info

    def test_block_with_rescue_and_always(self):
        task = {
            "name": "Error handling block",
            "block": [
                {"name": "Try this", "command": "do_something"},
                {"name": "And this", "command": "do_more"}
            ],
            "rescue": [
                {"name": "Handle error", "debug": {"msg": "failed"}}
            ],
            "always": [
                {"name": "Cleanup", "command": "cleanup"}
            ]
        }
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["type"] == "block"
        assert len(info["block_tasks"]) == 4
        names = [t["name"] for t in info["block_tasks"]]
        assert names == ["Try this", "And this", "Handle error", "Cleanup"]

    def test_block_only(self):
        task = {
            "name": "Simple block",
            "block": [
                {"name": "Task 1", "command": "cmd1"}
            ]
        }
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["type"] == "block"
        assert len(info["block_tasks"]) == 1

    def test_block_with_notify_on_block(self):
        task = {
            "name": "Notifying block",
            "notify": "Restart service",
            "block": [
                {"name": "Step 1", "command": "cmd"}
            ]
        }
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["type"] == "block"
        assert info["notify"] == ["Restart service"]

    def test_include_role(self):
        task = {"name": "Apply role", "include_role": {"name": "nginx"}}
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["type"] == "role"
        assert info["role_name"] == "nginx"

    def test_import_role(self):
        task = {"name": "Import role", "import_role": {"name": "common"}}
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["type"] == "role"
        assert info["role_name"] == "common"

    def test_include_tasks(self, tmp_dir):
        tasks_file = os.path.join(tmp_dir, "extra_tasks.yml")
        _write_yaml(tasks_file, [{"name": "Included task", "debug": {"msg": "hi"}}])

        task = {"name": "Include extra", "include_tasks": "extra_tasks.yml"}
        info = extract_task_info(task, tmp_dir, os.path.join(tmp_dir, "pb.yml"))
        assert info["type"] == "include"
        assert info["include_file"] == "extra_tasks.yml"
        assert len(info["included_tasks"]) == 1
        assert info["included_tasks"][0]["name"] == "Included task"

    def test_import_tasks(self, tmp_dir):
        tasks_file = os.path.join(tmp_dir, "imported.yml")
        _write_yaml(tasks_file, [{"name": "Imported task", "command": "ls"}])

        task = {"name": "Import extra", "import_tasks": "imported.yml"}
        info = extract_task_info(task, tmp_dir, os.path.join(tmp_dir, "pb.yml"))
        assert info["type"] == "include"
        assert info["include_file"] == "imported.yml"
        assert len(info["included_tasks"]) == 1

    def test_include_tasks_dict_form(self, tmp_dir):
        tasks_file = os.path.join(tmp_dir, "extra.yml")
        _write_yaml(tasks_file, [{"name": "Task", "debug": {"msg": "ok"}}])

        task = {"name": "Include dict", "include_tasks": {"file": "extra.yml"}}
        info = extract_task_info(task, tmp_dir, os.path.join(tmp_dir, "pb.yml"))
        assert info["type"] == "include"
        assert info["include_file"] == "extra.yml"

    def test_nested_block_in_block(self):
        task = {
            "name": "Outer block",
            "block": [
                {
                    "name": "Inner block",
                    "block": [
                        {"name": "Deep task", "command": "deep_cmd"}
                    ]
                }
            ]
        }
        info = extract_task_info(task, "/tmp", "/tmp/pb.yml")
        assert info["type"] == "block"
        assert len(info["block_tasks"]) == 1
        inner = info["block_tasks"][0]
        assert inner["type"] == "block"
        assert len(inner["block_tasks"]) == 1
        assert inner["block_tasks"][0]["name"] == "Deep task"


# ============================================================
# load_included_tasks
# ============================================================

class TestLoadIncludedTasks:
    def test_load_relative_to_base(self, tmp_dir):
        subdir = os.path.join(tmp_dir, "playbooks")
        os.makedirs(subdir)
        tasks_file = os.path.join(subdir, "extra.yml")
        _write_yaml(tasks_file, [{"name": "Relative task", "debug": {"msg": "ok"}}])

        result = load_included_tasks(tmp_dir, "extra.yml", os.path.join(subdir, "main.yml"))
        assert len(result) == 1
        assert result[0]["name"] == "Relative task"

    def test_load_relative_to_repo(self, tmp_dir):
        tasks_file = os.path.join(tmp_dir, "shared_tasks.yml")
        _write_yaml(tasks_file, [{"name": "Repo task", "command": "cmd"}])

        result = load_included_tasks(tmp_dir, "shared_tasks.yml", "/other/path/pb.yml")
        assert len(result) == 1

    def test_missing_file(self, tmp_dir):
        result = load_included_tasks(tmp_dir, "nonexistent.yml", "/tmp/pb.yml")
        assert result == []

    def test_empty_task_file(self):
        result = load_included_tasks("/tmp", "", "/tmp/pb.yml")
        assert result == []

    def test_invalid_yaml(self, tmp_dir):
        bad_file = os.path.join(tmp_dir, "bad.yml")
        _write_text(bad_file, ": : : invalid yaml [[[")
        result = load_included_tasks(tmp_dir, "bad.yml", os.path.join(tmp_dir, "pb.yml"))
        assert result == []


# ============================================================
# find_role_tasks
# ============================================================

class TestFindRoleTasks:
    def test_finds_tasks(self, tmp_dir):
        tasks_dir = os.path.join(tmp_dir, "roles", "nginx", "tasks")
        os.makedirs(tasks_dir)
        _write_yaml(os.path.join(tasks_dir, "main.yml"), [
            {"name": "Install nginx", "apt": {"name": "nginx"}},
            {"name": "Start nginx", "service": {"name": "nginx", "state": "started"}}
        ])
        tasks = find_role_tasks(tmp_dir, "nginx")
        assert len(tasks) == 2
        assert tasks[0]["name"] == "Install nginx"

    def test_yaml_extension(self, tmp_dir):
        tasks_dir = os.path.join(tmp_dir, "roles", "apache", "tasks")
        os.makedirs(tasks_dir)
        _write_yaml(os.path.join(tasks_dir, "main.yaml"), [
            {"name": "Install apache"}
        ])
        tasks = find_role_tasks(tmp_dir, "apache")
        assert len(tasks) == 1

    def test_role_not_found(self, tmp_dir):
        tasks = find_role_tasks(tmp_dir, "nonexistent")
        assert tasks == []

    def test_empty_tasks_file(self, tmp_dir):
        tasks_dir = os.path.join(tmp_dir, "roles", "empty", "tasks")
        os.makedirs(tasks_dir)
        _write_text(os.path.join(tasks_dir, "main.yml"), "")
        tasks = find_role_tasks(tmp_dir, "empty")
        assert tasks == []


# ============================================================
# find_role_dependencies
# ============================================================

class TestFindRoleDependencies:
    def test_string_dependencies(self, tmp_dir):
        meta_dir = os.path.join(tmp_dir, "roles", "webapp", "meta")
        os.makedirs(meta_dir)
        _write_yaml(os.path.join(meta_dir, "main.yml"), {
            "dependencies": ["common", "nginx"]
        })
        deps = find_role_dependencies(tmp_dir, "webapp")
        assert deps == ["common", "nginx"]

    def test_dict_dependencies_role_key(self, tmp_dir):
        meta_dir = os.path.join(tmp_dir, "roles", "webapp", "meta")
        os.makedirs(meta_dir)
        _write_yaml(os.path.join(meta_dir, "main.yml"), {
            "dependencies": [
                {"role": "common", "tags": ["base"]},
                {"role": "nginx"}
            ]
        })
        deps = find_role_dependencies(tmp_dir, "webapp")
        assert deps == ["common", "nginx"]

    def test_dict_dependencies_name_key(self, tmp_dir):
        meta_dir = os.path.join(tmp_dir, "roles", "app", "meta")
        os.makedirs(meta_dir)
        _write_yaml(os.path.join(meta_dir, "main.yml"), {
            "dependencies": [{"name": "base_role"}]
        })
        deps = find_role_dependencies(tmp_dir, "app")
        assert deps == ["base_role"]

    def test_no_dependencies(self, tmp_dir):
        meta_dir = os.path.join(tmp_dir, "roles", "standalone", "meta")
        os.makedirs(meta_dir)
        _write_yaml(os.path.join(meta_dir, "main.yml"), {
            "galaxy_info": {"author": "test"}
        })
        deps = find_role_dependencies(tmp_dir, "standalone")
        assert deps == []

    def test_no_meta_file(self, tmp_dir):
        deps = find_role_dependencies(tmp_dir, "nonexistent")
        assert deps == []

    def test_yaml_extension(self, tmp_dir):
        meta_dir = os.path.join(tmp_dir, "roles", "app", "meta")
        os.makedirs(meta_dir)
        _write_yaml(os.path.join(meta_dir, "main.yaml"), {
            "dependencies": ["base"]
        })
        deps = find_role_dependencies(tmp_dir, "app")
        assert deps == ["base"]


# ============================================================
# parse_playbook
# ============================================================

class TestParsePlaybook:
    def test_simple_playbook(self, tmp_dir):
        pb = [
            {
                "hosts": "webservers",
                "tasks": [
                    {"name": "Install nginx", "apt": {"name": "nginx"}}
                ]
            }
        ]
        path = os.path.join(tmp_dir, "deploy.yml")
        _write_yaml(path, pb)
        result = parse_playbook(path, tmp_dir)
        assert result["name"] == "deploy.yml"
        assert len(result["plays"]) == 1
        assert result["plays"][0]["hosts"] == "webservers"
        assert len(result["plays"][0]["tasks"]) == 1

    def test_roles_string_and_dict(self, tmp_dir):
        pb = [{
            "hosts": "all",
            "roles": [
                "common",
                {"role": "nginx", "tags": ["web"]},
                {"name": "postgres"}
            ]
        }]
        path = os.path.join(tmp_dir, "site.yml")
        _write_yaml(path, pb)
        result = parse_playbook(path, tmp_dir)
        assert result["plays"][0]["roles"] == ["common", "nginx", "postgres"]

    def test_pre_tasks_and_post_tasks(self, tmp_dir):
        pb = [{
            "hosts": "all",
            "pre_tasks": [{"name": "Pre task", "debug": {"msg": "pre"}}],
            "tasks": [{"name": "Main task", "debug": {"msg": "main"}}],
            "post_tasks": [{"name": "Post task", "debug": {"msg": "post"}}]
        }]
        path = os.path.join(tmp_dir, "pb.yml")
        _write_yaml(path, pb)
        result = parse_playbook(path, tmp_dir)
        task_names = [t["name"] for t in result["plays"][0]["tasks"]]
        assert task_names == ["Pre task", "Main task", "Post task"]

    def test_handlers(self, tmp_dir):
        pb = [{
            "hosts": "all",
            "tasks": [{"name": "Install", "apt": {"name": "nginx"}, "notify": "Restart nginx"}],
            "handlers": [
                {"name": "Restart nginx", "service": {"name": "nginx", "state": "restarted"}}
            ]
        }]
        path = os.path.join(tmp_dir, "pb.yml")
        _write_yaml(path, pb)
        result = parse_playbook(path, tmp_dir)
        assert result["plays"][0]["handlers"] == ["Restart nginx"]

    def test_import_playbook(self, tmp_dir):
        pb = [
            {"import_playbook": "common.yml"},
            {"import_playbook": "webservers.yml"},
            {"hosts": "all", "tasks": [{"name": "Final", "debug": {"msg": "done"}}]}
        ]
        path = os.path.join(tmp_dir, "site.yml")
        _write_yaml(path, pb)
        result = parse_playbook(path, tmp_dir)
        assert len(result["plays"]) == 1
        assert len(result["imported_playbooks"]) == 2
        assert result["imported_playbooks"][0].endswith("common.yml")
        assert result["imported_playbooks"][1].endswith("webservers.yml")

    def test_invalid_yaml(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.yml")
        _write_text(path, ": invalid [[[")
        with pytest.raises(ValueError, match="Ungültiges YAML"):
            parse_playbook(path, tmp_dir)

    def test_empty_playbook(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.yml")
        _write_text(path, "")
        result = parse_playbook(path, tmp_dir)
        assert result["plays"] == []
        assert result["imported_playbooks"] == []

    def test_multiple_plays(self, tmp_dir):
        pb = [
            {"hosts": "webservers", "tasks": [{"name": "Web task", "debug": {"msg": "web"}}]},
            {"hosts": "databases", "tasks": [{"name": "DB task", "debug": {"msg": "db"}}]}
        ]
        path = os.path.join(tmp_dir, "multi.yml")
        _write_yaml(path, pb)
        result = parse_playbook(path, tmp_dir)
        assert len(result["plays"]) == 2
        assert result["plays"][0]["hosts"] == "webservers"
        assert result["plays"][1]["hosts"] == "databases"


# ============================================================
# _collect_roles_from_tasks
# ============================================================

class TestCollectRolesFromTasks:
    def test_role_tasks(self):
        tasks = [
            {"type": "role", "role_name": "nginx", "name": "Apply nginx"},
            {"type": "task", "name": "Simple task"}
        ]
        roles = set()
        _collect_roles_from_tasks(tasks, roles)
        assert roles == {"nginx"}

    def test_roles_in_blocks(self):
        tasks = [{
            "type": "block",
            "name": "Block",
            "block_tasks": [
                {"type": "role", "role_name": "common", "name": "Apply common"},
                {"type": "task", "name": "Task"}
            ]
        }]
        roles = set()
        _collect_roles_from_tasks(tasks, roles)
        assert roles == {"common"}

    def test_roles_in_includes(self):
        tasks = [{
            "type": "include",
            "name": "Include",
            "include_file": "extra.yml",
            "included_tasks": [
                {"type": "role", "role_name": "webapp", "name": "Apply webapp"}
            ]
        }]
        roles = set()
        _collect_roles_from_tasks(tasks, roles)
        assert roles == {"webapp"}

    def test_nested_blocks_and_includes(self):
        tasks = [{
            "type": "block",
            "name": "Outer",
            "block_tasks": [{
                "type": "include",
                "name": "Inc",
                "include_file": "x.yml",
                "included_tasks": [
                    {"type": "role", "role_name": "deep_role", "name": "Deep"}
                ]
            }]
        }]
        roles = set()
        _collect_roles_from_tasks(tasks, roles)
        assert roles == {"deep_role"}

    def test_empty_tasks(self):
        roles = set()
        _collect_roles_from_tasks([], roles)
        assert roles == set()


# ============================================================
# parse_all (Integration)
# ============================================================

class TestParseAll:
    def _setup_repo(self, tmp_dir):
        """Erstellt eine vollständige Ansible-Repo-Struktur."""
        # Inventory
        inv_dir = os.path.join(tmp_dir, "inventory")
        os.makedirs(inv_dir)
        _write_yaml(os.path.join(inv_dir, "hosts.yml"), {
            "all": {
                "children": {
                    "webservers": {"hosts": {"web1": {}}},
                    "databases": {"hosts": {"db1": {}}}
                }
            }
        })

        # Roles
        for role_name in ["nginx", "common"]:
            tasks_dir = os.path.join(tmp_dir, "roles", role_name, "tasks")
            os.makedirs(tasks_dir)
            _write_yaml(os.path.join(tasks_dir, "main.yml"), [
                {"name": f"Task for {role_name}", "debug": {"msg": role_name}}
            ])

        # Role dependency: nginx depends on common
        meta_dir = os.path.join(tmp_dir, "roles", "nginx", "meta")
        os.makedirs(meta_dir)
        _write_yaml(os.path.join(meta_dir, "main.yml"), {
            "dependencies": ["common"]
        })

        # Playbook
        pb_dir = os.path.join(tmp_dir, "playbooks")
        os.makedirs(pb_dir)
        _write_yaml(os.path.join(pb_dir, "deploy.yml"), [
            {
                "hosts": "webservers",
                "roles": ["nginx"],
                "tasks": [
                    {"name": "Direct task", "debug": {"msg": "hi"}},
                    {
                        "name": "Error handling",
                        "block": [
                            {"name": "Try install", "apt": {"name": "pkg"}, "notify": "Restart service"}
                        ],
                        "rescue": [
                            {"name": "Handle error", "debug": {"msg": "error"}}
                        ]
                    }
                ],
                "handlers": [
                    {"name": "Restart service", "service": {"name": "myservice", "state": "restarted"}}
                ]
            }
        ])

        return {
            "inv": os.path.join(inv_dir, "hosts.yml"),
            "pb": os.path.join(pb_dir, "deploy.yml")
        }

    def test_full_parse(self, tmp_dir):
        paths = self._setup_repo(tmp_dir)
        data = parse_all([paths["inv"]], [paths["pb"]], tmp_dir)

        # Inventory
        assert "webservers" in data.groups
        assert "databases" in data.groups
        assert data.groups["webservers"] == ["web1"]

        # Roles (nginx direkt, common via dependency)
        assert "nginx" in data.roles
        assert "common" in data.roles

        # Role-Tasks
        assert len(data.role_tasks["nginx"]) == 1
        assert len(data.role_tasks["common"]) == 1

        # Role-Dependencies
        assert data.role_dependencies["nginx"] == ["common"]

        # Playbook
        assert len(data.playbooks) == 1
        pb = list(data.playbooks.values())[0]
        assert len(pb["plays"]) == 1
        play = pb["plays"][0]
        assert play["hosts"] == "webservers"
        assert play["roles"] == ["nginx"]
        assert len(play["tasks"]) == 2
        assert play["tasks"][1]["type"] == "block"

    def test_import_playbook_resolution(self, tmp_dir):
        pb_dir = os.path.join(tmp_dir, "playbooks")
        os.makedirs(pb_dir)

        # common.yml (importiertes Playbook)
        _write_yaml(os.path.join(pb_dir, "common.yml"), [
            {"hosts": "all", "tasks": [{"name": "Common task", "debug": {"msg": "common"}}]}
        ])

        # site.yml (importiert common.yml)
        _write_yaml(os.path.join(pb_dir, "site.yml"), [
            {"import_playbook": "common.yml"},
            {"hosts": "webservers", "tasks": [{"name": "Main task", "debug": {"msg": "main"}}]}
        ])

        # Inventory
        inv_path = os.path.join(tmp_dir, "hosts.yml")
        _write_yaml(inv_path, {"webservers": {"hosts": {"web1": {}}}})

        site_path = os.path.join(pb_dir, "site.yml")
        data = parse_all([inv_path], [site_path], tmp_dir)

        # Beide Playbooks sollten geparst sein
        assert len(data.playbooks) == 2
        playbook_names = {pb["name"] for pb in data.playbooks.values()}
        assert "site.yml" in playbook_names
        assert "common.yml" in playbook_names

    def test_duplicate_playbook_not_parsed_twice(self, tmp_dir):
        pb_dir = os.path.join(tmp_dir, "playbooks")
        os.makedirs(pb_dir)

        _write_yaml(os.path.join(pb_dir, "shared.yml"), [
            {"hosts": "all", "tasks": [{"name": "Shared", "debug": {"msg": "shared"}}]}
        ])
        _write_yaml(os.path.join(pb_dir, "a.yml"), [{"import_playbook": "shared.yml"}])
        _write_yaml(os.path.join(pb_dir, "b.yml"), [{"import_playbook": "shared.yml"}])

        inv_path = os.path.join(tmp_dir, "hosts.yml")
        _write_yaml(inv_path, {"all": {"hosts": {"h1": {}}}})

        a_path = os.path.join(pb_dir, "a.yml")
        b_path = os.path.join(pb_dir, "b.yml")
        data = parse_all([inv_path], [a_path, b_path], tmp_dir)

        # shared.yml sollte nur einmal vorkommen
        shared_count = sum(1 for pb in data.playbooks.values() if pb["name"] == "shared.yml")
        assert shared_count == 1
