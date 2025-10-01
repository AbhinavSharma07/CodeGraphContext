import subprocess
import json
import os
import time
import pytest

SAMPLE_PROJECT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "sample_project")
)

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
ENV_CONTENT = """\
NEO4J_URI=neo4j+s://44df5fd5.databases.neo4j.io
NEO4J_USERNAME=44df5fd5
NEO4J_PASSWORD=vSwK0dBCmaaMEQKFvWWFc7bPAdYlMAXFBlND-Tj-OEA
"""

TIMEOUT = 180  # seconds


def call_tool(server, name, args):
    """Send a tool request to the server and parse the response."""
    request = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": "tools/call",
        "params": {"name": name, "arguments": args}
    }
    response = server(request)
    try:
        return json.loads(response["result"]["content"][0]["text"])
    except Exception as e:
        pytest.fail(f"Failed to parse server response: {response}, error: {e}")


@pytest.fixture(scope="module")
def server():
    """Starts the CGC server process and provides a JSON-RPC interface to it."""
    print("\n[Setup] Starting cgc server...")

    # 1. Write .env
    with open(ENV_PATH, "w") as f:
        f.write(ENV_CONTENT)
    print(f"[Setup] .env file written at {ENV_PATH}")

    process = None
    try:
        process = subprocess.Popen(
            ["cgc", "start"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.path.dirname(ENV_PATH),
            bufsize=1  # Line-buffered
        )

        # 2. Wait for server to be ready
        for line in iter(process.stderr.readline, ''):
            print(f"[CGC STDERR] {line.strip()}")
            if "MCP Server is running" in line:
                break
        else:
            pytest.fail("Server did not start successfully or timed out.")

        def send_receive(request):
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            while True:
                line = process.stdout.readline()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

        # 3. Initialize
        init = send_receive({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init.get("id") == 1 and "result" in init, "Failed to initialize server connection"

        yield send_receive

    finally:
        print("\n[Teardown] Cleaning up...")
        if process:
            process.terminate()
            process.wait(timeout=10)
            print("[Teardown] Server process terminated.")
        if os.path.exists(ENV_PATH):
            os.remove(ENV_PATH)
            print("[Teardown] .env file removed.")


@pytest.fixture(scope="module")
def indexed_project(server):
    """Ensure project is re-indexed before testing."""
    print("\n[Indexing] Starting re-indexing of project...")

    delete_result = call_tool(server, "delete_repository", {"repo_path": SAMPLE_PROJECT_PATH})
    print(f"[Indexing] Deleted previous repo state: {delete_result}")

    add_result = call_tool(server, "add_code_to_graph", {"path": SAMPLE_PROJECT_PATH})
    assert add_result.get("success") is True, f"add_code_to_graph failed: {add_result}"
    job_id = add_result.get("job_id")
    assert job_id, "Job ID not returned from add_code_to_graph"

    # Wait for job to complete
    start_time = time.time()
    while True:
        if time.time() - start_time > TIMEOUT:
            pytest.fail(f"Indexing job {job_id} timed out after {TIMEOUT} seconds.")

        status = call_tool(server, "check_job_status", {"job_id": job_id})
        job_status = status.get("job", {}).get("status")
        print(f"[Indexing] Job status: {job_status}")

        if job_status == "completed":
            break
        elif job_status in ("failed", "cancelled"):
            pytest.fail(f"Indexing job failed with status: {job_status}")
        time.sleep(2)

    return server
