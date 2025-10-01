import subprocess
import json
import os
import time
import pytest

# --- CONFIGURATION ---
SAMPLE_PROJECT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "sample_project"))
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
ENV_CONTENT = """NEO4J_URI=neo4j+s://44df5fd5.databases.neo4j.io
NEO4J_USERNAME=44df5fd5
NEO4J_PASSWORD=vSwK0dBCmaaMEQKFvWWFc7bPAdYlMAXFBlND-Tj-OEA
"""

# --- HELPER FUNCTION ---
def call_tool(server, name, args):
    request = {
        "jsonrpc": "2.0",
        "id": int(time.time()),
        "method": "tools/call",
        "params": {"name": name, "arguments": args}
    }
    response = server(request)
    if "result" in response:
        return json.loads(response["result"]["content"][0]["text"])
    elif "error" in response:
        return response["error"].get("data", {})
    else:
        raise ValueError(f"Unexpected response format: {response}")

# --- FIXTURE: Start CGC Server ---
@pytest.fixture(scope="module")
def server():
    print("\n--- Setting up server fixture ---")
    with open(ENV_PATH, "w") as f:
        f.write(ENV_CONTENT)
    print(f"Created .env at: {ENV_PATH}")

    process = subprocess.Popen(
        ["cgc", "start"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=os.path.join(os.path.dirname(__file__), "..")
    )

    try:
        print("Waiting for server to be ready...")
        for line in iter(process.stderr.readline, ''):
            print(f"STDERR: {line.strip()}")
            if "MCP Server is running" in line:
                print("Server is ready.")
                break

        def send_receive(request):
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            while True:
                response_line = process.stdout.readline()
                try:
                    return json.loads(response_line)
                except json.JSONDecodeError:
                    continue

        # Initialize server
        init_response = send_receive({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init_response.get("id") == 1 and "result" in init_response, "Failed to initialize server."

        yield send_receive

    finally:
        print("\n--- Tearing down server ---")
        process.terminate()
        process.wait()
        if os.path.exists(ENV_PATH):
            os.remove(ENV_PATH)
            print("Removed .env file.")

# --- FIXTURE: Index Sample Project ---
@pytest.fixture(scope="module")
def indexed_project(server):
    skip_reindex = os.environ.get("CGC_SKIP_REINDEX", "false").lower() == "true"

    if skip_reindex:
        print("\n--- Skipping re-index (cached) ---")
        repos = call_tool(server, "list_indexed_repositories", {})
        if SAMPLE_PROJECT_PATH in [r.get("path") for r in repos.get("repositories", [])]:
            return server

    print("\n--- Re-indexing project ---")
    call_tool(server, "delete_repository", {"repo_path": SAMPLE_PROJECT_PATH})

    add_result = call_tool(server, "add_code_to_graph", {"path": SAMPLE_PROJECT_PATH})
    assert add_result.get("success"), f"Indexing failed: {add_result}"
    job_id = add_result.get("job_id")
    assert job_id, "No job_id returned"

    timeout = 180
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            pytest.fail("Indexing timed out")
        status = call_tool(server, "check_job_status", {"job_id": job_id})
        job_status = status.get("job", {}).get("status")
        if job_status == "completed":
            break
        assert job_status not in ["failed", "cancelled"], f"Indexing job failed: {job_status}"
        time.sleep(2)

    return server

# --- TEST CASES ---

def test_list_indexed_repositories(indexed_project):
    result = call_tool(indexed_project, "list_indexed_repositories", {})
    assert result.get("success")
    paths = [r["path"] for r in result.get("repositories", [])]
    assert SAMPLE_PROJECT_PATH in paths

def test_find_code_function(indexed_project):
    result = call_tool(indexed_project, "find_code", {"query": "foo"})
    assert result.get("success")
    results = result.get("results", {}).get("ranked_results", [])
    assert any("module_a.py" in r.get("file_path", "") and r.get("name") == "foo" for r in results)

def test_find_code_class(indexed_project):
    result = call_tool(indexed_project, "find_code", {"query": "Dummy"})
    assert result.get("success")
    results = result.get("results", {}).get("ranked_results", [])
    assert any("advanced_calls.py" in r.get("file_path", "") and r.get("name") == "Dummy" for r in results)

def test_find_callers(indexed_project):
    result = call_tool(indexed_project, "analyze_code_relationships", {
        "query_type": "find_callers", "target": "helper"
    })
    assert result.get("success")
    callers = {r["caller_function"] for r in result["results"]["results"]}
    assert {"foo", "call_helper_twice"}.issubset(callers)

def test_find_callees(indexed_project):
    result = call_tool(indexed_project, "analyze_code_relationships", {
        "query_type": "find_callees", "target": "foo",
        "context": os.path.join(SAMPLE_PROJECT_PATH, "module_a.py")
    })
    callees = {r["called_function"] for r in result["results"]["results"]}
    assert {"helper", "process_data"}.issubset(callees)

def test_class_hierarchy(indexed_project):
    result = call_tool(indexed_project, "analyze_code_relationships", {
        "query_type": "class_hierarchy", "target": "C",
        "context": os.path.join(SAMPLE_PROJECT_PATH, "advanced_classes.py")
    })
    hierarchy = result["results"]["results"]
    parents = {p["parent_class"] for p in hierarchy.get("parent_classes", [])}
    assert {"A", "B"}.issubset(parents)

def test_find_importers(indexed_project):
    result = call_tool(indexed_project, "analyze_code_relationships", {
        "query_type": "find_importers", "target": "module_b"
    })
    files = {r["file_name"] for r in result["results"]["results"]}
    assert {"module_a.py", "submodule1.py"}.issubset(files)

def test_module_dependencies(indexed_project):
    result = call_tool(indexed_project, "analyze_code_relationships", {
        "query_type": "module_deps", "target": "module_a"
    })
    assert result["results"]["results"]["module_name"] == "module_a"

def test_list_imports(indexed_project):
    result = call_tool(indexed_project, "list_imports", {
        "path": os.path.join(SAMPLE_PROJECT_PATH, "module_a.py")
    })
    assert "math" in result.get("imports", [])
    assert "module_b" in result.get("imports", [])

def test_find_dead_code(indexed_project):
    result = call_tool(indexed_project, "find_dead_code", {})
    assert result.get("success")

def test_cyclomatic_complexity(indexed_project):
    result = call_tool(indexed_project, "calculate_cyclomatic_complexity", {
        "function_name": "try_except_finally"
    })
    assert result.get("success")
    assert result["results"][0].get("complexity") == 4

def test_most_complex_functions(indexed_project):
    result = call_tool(indexed_project, "find_most_complex_functions", {"limit": 5})
    names = {f["function_name"] for f in result["results"]}
    assert "try_except_finally" in names

def test_execute_cypher_query(indexed_project):
    query = "MATCH (n:Function) RETURN n.name AS functionName LIMIT 5"
    result = call_tool(indexed_project, "execute_cypher_query", {"cypher_query": query})
    assert result.get("success")
    assert "functionName" in result["results"][0]

def test_cypher_query_with_keyword_in_string(indexed_project):
    query = "MATCH (n:Function) WHERE n.name = 'create_user_function' RETURN n.name AS functionName"
    result = call_tool(indexed_project, "execute_cypher_query", {"cypher_query": query})
    assert result.get("success"), f"Query failed: {result.get('error')}"

def test_cypher_query_with_write_operation(indexed_project):
    query = "CREATE (n:TestNode) RETURN n"
    result = call_tool(indexed_project, "execute_cypher_query", {"cypher_query": query})
    assert result.get("success") is None, "Write query should have failed but succeeded"
    assert "error" in result
    assert "read-only" in result.get("error", "").lower(), "Expected read-only error message"
