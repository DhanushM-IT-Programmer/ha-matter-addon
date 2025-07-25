# File: matter_server.py

from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess

app = Flask(__name__)
CORS(app)  # Allow all origins

CHIP_TOOL_PATH = "/app/connected_home_ip/out/chip-tool-linux/chip-tool"
# Adjust as needed


def run_chip_tool(args):
    cmd = [CHIP_TOOL_PATH] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60)
        return {
            "command": " ".join(cmd),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except Exception as e:
        return {"error": str(e)}


@app.route('/bind', methods=['POST'])
def bind_device():
    data = request.get_json()
    switch_node = str(data.get('switch_node'))
    switch_endpoint = str(data.get('switch_endpoint', 1))
    light_node = str(data.get('light_node'))
    light_endpoint = str(data.get('light_endpoint', 1))

    if not (switch_node and light_node):
        return jsonify({"error": "Missing switch_node or light_node"}), 400

    # 1. accesscontrol write acl command
    acl_json = (
        '[{"fabricIndex": 1, "privilege": 5, "authMode": 2, "subjects": null, "targets": null}, '
        f'{{"fabricIndex": 1, "privilege": 3, "authMode": 2, "subjects": [{switch_node}], "targets": null}}]'
    )

    acl_result = run_chip_tool([
        "accesscontrol", "write", "acl",
        acl_json,
        light_node,
        "0"
    ])

    # 2. binding write binding command
    binding_json = f'[{{"node":{light_node}, "endpoint":{light_endpoint}, "cluster":6}}]'
    binding_result = run_chip_tool([
        "binding", "write", "binding",
        binding_json,
        switch_node,
        switch_endpoint
    ])

    return jsonify({
        "acl_command": acl_result,
        "binding_command": binding_result
    })


@app.route('/pair', methods=['POST'])
def pair_device():
    data = request.get_json()
    node_id = str(data.get('node_id'))
    passcode = str(data.get('passcode'))

    if not node_id or not passcode:
        return jsonify({"error": "Missing node_id or passcode"}), 400

    result = run_chip_tool(["pairing", "code", node_id, passcode])
    return jsonify(result)

@app.route('/command', methods=['POST'])
def run_custom_command():
    data = request.get_json()

    if not data or 'args' not in data:
        return jsonify({"error": "Missing 'args' in request body"}), 400

    args = data['args']
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return jsonify({"error": "'args' must be a list of strings"}), 400

    result = run_chip_tool(args)
    return jsonify(result)

@app.route('/toggle', methods=['POST'])
def toggle_device():
    data = request.get_json()
    node_id = str(data.get('node_id'))
    endpoint = str(data.get('endpoint', 1))  # default to endpoint 1

    result = run_chip_tool(["onoff", "toggle", node_id, endpoint])
    return jsonify(result)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=6000)
