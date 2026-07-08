import networkx as nx
from .models import (
    WeatherMap
)

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

    # Sets MapNode positions based on scaled version of kamada_kawai_layout positions
    for name, (nx_x, nx_y) in pos.items():
        node = wm.nodes[name]
        if node.x is None:
            node.x = cx + nx_x * x_scale
            node.y = cy + nx_y * y_scale