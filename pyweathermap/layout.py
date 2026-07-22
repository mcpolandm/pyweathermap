import networkx as nx
import math
from .models import (
    WeatherMap
)
MIN_ANGLE_GAP = math.radians(6)
DECONFLICT_PASSES = 6
SPARSE_NODE_THRESHOLD = 8

# Called by config.py at end of WeatherMap initialization to set MapNode positions.
# Uses NetworkX function kamada_kawai_layout to limit link overlap.
# Then scales up [-1,1] scaled output to the scale of the diagram.
def auto_layout(wm: WeatherMap, margin: int=80):
    # Initialize Graph object for layout function
    graph = nx.Graph()
    graph.add_nodes_from(wm.nodes)
    graph.add_edges_from((link.node1, link.node2) for link in wm.links.values())

    pos = nx.kamada_kawai_layout(graph)

    # Computes center and scales based on diagram size
    cx = wm.width / 2
    cy = wm.height / 2
    x_scale = wm.width / 2 - margin
    y_scale = wm.height / 2 - margin

    node_count = len(wm.nodes)
    if node_count < SPARSE_NODE_THRESHOLD:
        size_factor = math.sqrt(node_count / SPARSE_NODE_THRESHOLD)
        x_scale *= size_factor
        y_scale *= size_factor

    # Sets MapNode positions based on scaled version of kamada_kawai_layout positions
    for name, (nx_x, nx_y) in pos.items():
        node = wm.nodes[name]
        if node.x is None:
            node.x = cx + nx_x * x_scale
            node.y = cy + nx_y * y_scale
    
    deconflict_hub_angles(wm)

def deconflict_hub_angles(wm: WeatherMap):
    for hub in wm.nodes.values():
        if hub.node_type != "switch":
            continue

        neighbors = [
            wm.nodes[link.node2 if link.node1 == hub.name else link.node1]
            for link in wm.links.values()
            if hub.name in (link.node1, link.node2)
        ]
        if len(neighbors) < 2:
            continue

        polar = []
        for n in neighbors:
            dx, dy = n.x - hub.x, n.y - hub.y
            polar.append([n, math.hypot(dx, dy), math.atan2(dy, dx)])
        polar.sort(key=lambda p: p[2])

        for _ in range(DECONFLICT_PASSES):
            moved = False
            for i in range(1, len(polar)):
                gap = polar[i][2] - polar[i - 1][2]
                if gap < MIN_ANGLE_GAP:
                    deficit = (MIN_ANGLE_GAP - gap) / 2
                    polar[i - 1][2] -= deficit
                    polar[i][2] += deficit
                    moved = True
            if not moved:
                break

        for n, r, theta in polar:
            n.x = hub.x + r * math.cos(theta)
            n.y = hub.y + r * math.sin(theta)