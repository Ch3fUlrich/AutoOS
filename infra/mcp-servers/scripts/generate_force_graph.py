import json
import sys
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Generate a Force-Directed Graph HTML from graphify output.")
    parser.add_argument("--graph", type=str, default="graphify-out/graph.json", help="Path to input graph.json")
    parser.add_argument("--out", type=str, default="graphify-out/GRAPH_FORCE_DIRECTED.html", help="Path to output HTML")
    args = parser.parse_args()

    graph_path = Path(args.graph)
    out_path = Path(args.out)

    if not graph_path.exists():
        print(f"Error: {graph_path} not found.", file=sys.stderr)
        sys.exit(1)

    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = []
    edges = []

    for node in data.get("nodes", []):
        nodes.append({
            "id": node["id"],
            "label": node.get("label", node["id"]),
            "group": node.get("community", 0),
            "title": f"Type: {node.get('type', 'unknown')}<br>File: {node.get('source_file', '')}"
        })
    
    # Depending on the graph.json version, links could be 'links' or 'edges'
    links = data.get("links", data.get("edges", []))
    for edge in links:
        edges.append({
            "from": edge.get("source"),
            "to": edge.get("target"),
            "label": edge.get("relation", ""),
            "arrows": "to"
        })

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Force-Directed Graph</title>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style type="text/css">
        #mynetwork {{
            width: 100vw;
            height: 100vh;
            border: 1px solid lightgray;
            background-color: #f9f9f9;
        }}
        body {{ margin: 0; padding: 0; font-family: sans-serif; overflow: hidden; }}
        #header {{
            position: absolute;
            top: 10px;
            left: 10px;
            background: rgba(255,255,255,0.8);
            padding: 10px;
            border-radius: 5px;
            box-shadow: 0 0 5px rgba(0,0,0,0.3);
            z-index: 100;
        }}
    </style>
</head>
<body>
<div id="header">
    <h3>Force-Directed Graph</h3>
    <p>Scroll to zoom, drag to pan and move nodes.</p>
</div>
<div id="mynetwork"></div>
<script type="text/javascript">
    var nodes = new vis.DataSet({json.dumps(nodes)});
    var edges = new vis.DataSet({json.dumps(edges)});

    var container = document.getElementById('mynetwork');
    var data = {{
        nodes: nodes,
        edges: edges
    }};
    var options = {{
        nodes: {{
            shape: 'dot',
            size: 16,
            font: {{
                size: 14,
                color: '#333'
            }},
            borderWidth: 2,
            shadow: true
        }},
        edges: {{
            font: {{
                size: 10,
                align: 'middle'
            }},
            color: {{inherit: 'from'}},
            smooth: {{
                type: 'continuous'
            }}
        }},
        physics: {{
            forceAtlas2Based: {{
                gravitationalConstant: -50,
                centralGravity: 0.01,
                springLength: 100,
                springConstant: 0.08
            }},
            maxVelocity: 50,
            solver: 'forceAtlas2Based',
            timestep: 0.35,
            stabilization: {{iterations: 150}}
        }}
    }};
    var network = new vis.Network(container, data, options);
</script>
</body>
</html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"Force-Directed Graph generated successfully at: {out_path}")

if __name__ == "__main__":
    main()
