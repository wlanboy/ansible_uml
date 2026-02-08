"""Tests für mermaid_generator.py."""

import pytest

from ansible_parser import AnsibleData
from mermaid_generator import (
    DiagramNodes,
    _apply_classes,
    _process_task,
    escape_label,
    generate_diagram,
    sanitize,
)


# ============================================================
# sanitize
# ============================================================

class TestSanitize:
    def test_simple_text(self):
        assert sanitize("webservers") == "webservers"

    def test_dots_replaced(self):
        assert sanitize("web1.example.com") == "web1_example_com"

    def test_special_chars(self):
        assert sanitize("my-group/name") == "my-group_name"

    def test_multiple_underscores_collapsed(self):
        assert sanitize("a___b") == "a_b"

    def test_leading_digit(self):
        assert sanitize("123host") == "id_123host"

    def test_leading_digit_with_special(self):
        assert sanitize("1.2.3.4") == "id_1_2_3_4"

    def test_whitespace_stripped(self):
        assert sanitize("  hello  ") == "hello"

    def test_hyphen_preserved(self):
        assert sanitize("my-role") == "my-role"

    def test_empty_after_strip(self):
        result = sanitize("   ")
        assert isinstance(result, str)


# ============================================================
# escape_label
# ============================================================

class TestEscapeLabel:
    def test_no_quotes(self):
        assert escape_label("hello world") == "hello world"

    def test_double_quotes_escaped(self):
        assert escape_label('say "hello"') == "say 'hello'"

    def test_non_string_input(self):
        assert escape_label(42) == "42"

    def test_empty_string(self):
        assert escape_label("") == ""


# ============================================================
# _process_task
# ============================================================

class TestProcessTask:
    def _run(self, task, parent_id="pb"):
        lines = []
        nodes = DiagramNodes()
        connections = []
        counter = _process_task(task, parent_id, lines, nodes, connections, 0)
        return lines, nodes, connections, counter

    def test_simple_task(self):
        task = {"name": "Install nginx", "type": "task"}
        lines, nodes, connections, counter = self._run(task)
        assert counter == 1
        assert any("Install nginx" in l for l in lines)
        assert len(nodes.tasks) == 1

    def test_role_task(self):
        task = {"name": "Apply nginx", "type": "role", "role_name": "nginx"}
        lines, nodes, connections, counter = self._run(task)
        assert counter == 1
        assert sanitize("role_nginx") in nodes.roles
        assert any("==>" in c for c in connections)

    def test_include_task(self):
        task = {
            "name": "Include extra",
            "type": "include",
            "include_file": "extra_tasks.yml",
            "included_tasks": [
                {"name": "Subtask 1", "type": "task"},
                {"name": "Subtask 2", "type": "task"}
            ]
        }
        lines, nodes, connections, counter = self._run(task)
        assert len(nodes.includes) == 1
        assert any("extra_tasks" in l for l in lines)

    def test_block_task(self):
        task = {
            "name": "Error handling",
            "type": "block",
            "block_tasks": [
                {"name": "Try step", "type": "task"},
                {"name": "Rescue step", "type": "task"}
            ]
        }
        lines, nodes, connections, counter = self._run(task)
        assert any("Error handling" in l for l in lines)
        assert any("Try step" in l for l in lines)
        assert any("Rescue step" in l for l in lines)
        # Block-Node + 2 Kind-Tasks
        assert len(nodes.tasks) == 3

    def test_task_with_notify(self):
        task = {
            "name": "Install pkg",
            "type": "task",
            "notify": ["Restart service"]
        }
        lines, nodes, connections, counter = self._run(task)
        assert any("notifies" in c for c in connections)
        handler_id = sanitize("handler_Restart service")
        assert handler_id in nodes.handlers

    def test_task_with_multiple_notifies(self):
        task = {
            "name": "Configure",
            "type": "task",
            "notify": ["Restart nginx", "Reload config"]
        }
        lines, nodes, connections, counter = self._run(task)
        notify_connections = [c for c in connections if "notifies" in c]
        assert len(notify_connections) == 2
        assert len(nodes.handlers) == 2

    def test_nested_block_with_role(self):
        task = {
            "name": "Deploy block",
            "type": "block",
            "block_tasks": [
                {"name": "Apply role", "type": "role", "role_name": "webapp"}
            ]
        }
        lines, nodes, connections, counter = self._run(task)
        assert sanitize("role_webapp") in nodes.roles
        assert any("==>" in c for c in connections)

    def test_counter_increments(self):
        task = {"name": "Task", "type": "task"}
        _, _, _, counter = self._run(task)
        assert counter == 1


# ============================================================
# _apply_classes
# ============================================================

class TestApplyClasses:
    def test_all_classes_applied(self):
        lines = []
        nodes = DiagramNodes(
            groups={"g1"},
            hosts={"h1"},
            playbooks=["pb1"],
            roles={"r1"},
            tasks=["t1"],
            handlers={"hd1"},
            includes={"i1"}
        )
        _apply_classes(lines, nodes)
        text = "\n".join(lines)
        assert "groupClass" in text
        assert "hostClass" in text
        assert "playbookClass" in text
        assert "roleClass" in text
        assert "taskClass" in text
        assert "handlerClass" in text
        assert "includeClass" in text

    def test_empty_nodes(self):
        lines = []
        nodes = DiagramNodes()
        _apply_classes(lines, nodes)
        assert lines == []

    def test_partial_nodes(self):
        lines = []
        nodes = DiagramNodes(groups={"g1"}, hosts={"h1"})
        _apply_classes(lines, nodes)
        assert len(lines) == 2
        assert "groupClass" in lines[0]
        assert "hostClass" in lines[1]


# ============================================================
# generate_diagram (Integration)
# ============================================================

class TestGenerateDiagram:
    def _minimal_data(self):
        data = AnsibleData()
        data.groups = {"webservers": ["web1"]}
        data.roles = {"nginx"}
        data.role_tasks = {"nginx": [{"name": "Install nginx"}]}
        data.playbooks = {
            "/tmp/deploy.yml": {
                "name": "deploy.yml",
                "path": "/tmp/deploy.yml",
                "plays": [{
                    "hosts": "webservers",
                    "roles": ["nginx"],
                    "tasks": [],
                    "handlers": []
                }],
                "imported_playbooks": []
            }
        }
        return data

    def test_basic_structure(self):
        data = self._minimal_data()
        diagram = generate_diagram(data)
        assert diagram.startswith("graph LR")
        assert 'subgraph inventory["Inventory"]' in diagram
        assert 'subgraph playbooks_section["Playbooks"]' in diagram
        assert 'subgraph roles_section["Roles"]' in diagram

    def test_layout_parameter(self):
        data = self._minimal_data()
        for layout in ["TD", "LR", "BT", "RL"]:
            diagram = generate_diagram(data, layout=layout)
            assert diagram.startswith(f"graph {layout}")

    def test_inventory_nodes(self):
        data = self._minimal_data()
        diagram = generate_diagram(data)
        assert "fa:fa-layer-group webservers" in diagram
        assert "fa:fa-server web1" in diagram

    def test_playbook_nodes(self):
        data = self._minimal_data()
        diagram = generate_diagram(data)
        assert "fa:fa-book deploy.yml" in diagram

    def test_role_nodes(self):
        data = self._minimal_data()
        diagram = generate_diagram(data)
        assert "fa:fa-cube nginx" in diagram
        assert "Install nginx" in diagram

    def test_runs_connection(self):
        data = self._minimal_data()
        diagram = generate_diagram(data)
        assert '"runs"' in diagram

    def test_uses_connection(self):
        data = self._minimal_data()
        diagram = generate_diagram(data)
        assert '"uses"' in diagram

    def test_styles_present(self):
        data = self._minimal_data()
        diagram = generate_diagram(data)
        assert "classDef groupClass" in diagram
        assert "classDef hostClass" in diagram
        assert "classDef playbookClass" in diagram
        assert "classDef roleClass" in diagram
        assert "classDef taskClass" in diagram
        assert "classDef handlerClass" in diagram

    def test_handler_nodes(self):
        data = self._minimal_data()
        data.playbooks["/tmp/deploy.yml"]["plays"][0]["handlers"] = ["Restart nginx"]
        diagram = generate_diagram(data)
        assert "fa:fa-bell Restart nginx" in diagram

    def test_notify_connections(self):
        data = self._minimal_data()
        data.playbooks["/tmp/deploy.yml"]["plays"][0]["tasks"] = [
            {"name": "Install pkg", "type": "task", "notify": ["Restart nginx"]}
        ]
        data.playbooks["/tmp/deploy.yml"]["plays"][0]["handlers"] = ["Restart nginx"]
        diagram = generate_diagram(data)
        assert '"notifies"' in diagram

    def test_block_rendering(self):
        data = self._minimal_data()
        data.playbooks["/tmp/deploy.yml"]["plays"][0]["tasks"] = [{
            "name": "Error handling",
            "type": "block",
            "block_tasks": [
                {"name": "Try step", "type": "task"},
                {"name": "Rescue step", "type": "task"}
            ]
        }]
        diagram = generate_diagram(data)
        assert "Error handling" in diagram
        assert "Try step" in diagram
        assert "Rescue step" in diagram

    def test_import_playbook_connection(self):
        data = self._minimal_data()
        data.playbooks["/tmp/deploy.yml"]["imported_playbooks"] = ["/tmp/common.yml"]
        diagram = generate_diagram(data)
        assert '"imports"' in diagram

    def test_role_dependencies(self):
        data = self._minimal_data()
        data.roles = {"nginx", "common"}
        data.role_tasks = {
            "nginx": [{"name": "Install nginx"}],
            "common": [{"name": "Base setup"}]
        }
        data.role_dependencies = {"nginx": ["common"]}
        diagram = generate_diagram(data)
        assert '"depends"' in diagram

    def test_empty_data(self):
        data = AnsibleData()
        diagram = generate_diagram(data)
        assert "graph LR" in diagram
        assert "subgraph" in diagram

    def test_multiple_groups_and_hosts(self):
        data = self._minimal_data()
        data.groups = {
            "webservers": ["web1", "web2"],
            "databases": ["db1"]
        }
        diagram = generate_diagram(data)
        assert "web1" in diagram
        assert "web2" in diagram
        assert "db1" in diagram
        assert "webservers" in diagram
        assert "databases" in diagram

    def test_class_assignments(self):
        data = self._minimal_data()
        diagram = generate_diagram(data)
        assert "class " in diagram
        assert "groupClass" in diagram
