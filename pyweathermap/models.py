from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict

# Defines Color dataclass, which stores r, g, and b values for a Color.
# Can return as an RGB tuple with as_tuple.
# Used to easily define colors for MapScale, MapNode, and MapLink
@dataclass
class Color:
    r: int
    g: int
    b: int

    def as_tuple(self) -> Tuple[int, int, int]:
        return (self.r, self.g, self.b)


# Defines a collection of low,high,Color entries that fill the scale from 0-100%.
# color_for_percentage determines the correct entry for that percentage.
@dataclass
class MapScale:
    name: str
    entries: List[Tuple[float, float, Color]] = field(default_factory=list)

    def color_for_percentage(self, pct: float) -> Color:
        pct = max(0.0, min(100.0, pct))
        for low, high, color in sorted(self.entries, key=lambda e: e[0]):
            if low <= pct <= high:
                return color
        return Color(192, 192, 192)


# Defines a singular node (switch or endpoint) in the map.
# Contains name, label, type, position values, and icon details for all nodes.
# Switch nodes have ip and community set for use by SNMP.
# infourl allows clickable link on that node, used to redirect to other pages.
@dataclass
class MapNode:
    name: str
    label: str = ""
    node_type: str = "endpoint"
    x: float = None
    y: float = None
    icon_width: int = 44
    icon_height: int = 22
    icon_type: str = "box"
    infourl: Optional[str] = None # only set for undug switches (links to other WeatherMaps)
    ip: Optional[str] = None # only set for switches
    community: Optional[str] = None # only set for switches


# Defines a singular link between two MapNodes.
# Contains name, node1 (always switch) and node2, and bandwidth.
# width and bwlabel are never changed.
# in/out_bps define in/out traffic values, 
# with in/out_color as the corresponding colors for those percentages of bandwidth.
# snmp_index is saved for SNMP commands.
@dataclass
class MapLink:
    name: str
    node1: str = ""
    node2: str = ""
    bandwidth: float = 0.0
    width: int = 4
    bwlabel: str = "bits"
    snmp_index: Optional[str] = None # set to ifIndex for switch connection to endpoint
    iface1: Optional[str] = None # interface name on node1
    iface2: Optional[str] = None # interface name on node2
    in_bps: float = 0.0
    out_bps: float = 0.0
    in_color: Color = field(default_factory=lambda: Color(192, 192, 192))
    out_color: Color = field(default_factory=lambda: Color(192, 192, 192))

# Sets the default scale color values for the WeatherMap.
# Ranges from white for <0.1%, and then purple to red from 0.1% to 100%.
def _default_scale() -> MapScale:
    # gray=no data, white=0-0.1%, then purple→blue→green→yellow→orange→red at 100%
    s = MapScale("DEFAULT")
    s.entries = [
        (0, 0, Color(192, 192, 192)),   # no traffic — gray
        (0, 0.1, Color(255, 255, 255)),    # near-zero — white
        (0.1, 10, Color(140, 0, 255)),
        (10, 25, Color(0, 0, 255)),
        (25, 40, Color(0, 152, 255)),
        (40, 55, Color(0, 220, 0)),
        (55, 70, Color(255, 255, 0)),
        (70, 85, Color(255, 128, 0)),
        (85, 100, Color(255, 0, 0)),     # saturated — red
    ]
    return s

# Defines the WeatherMap object which contains all information to build the diagram.
# nodes and links contain a list of all MapNodes and MapLinks in the Map.
# Scale contains the default scale.
# All other values are defaults and unchanged.
@dataclass
class WeatherMap:
    width: int = 2000
    height: int = 2000
    title: str = "My Network Map"
    bgcolor: Color = field(default_factory=lambda: Color(240, 245, 255))
    title_color: Color = field(default_factory=lambda: Color(0, 0, 0))
    time_color: Color = field(default_factory=lambda: Color(128, 128, 128))
    nodes: Dict[str, MapNode] = field(default_factory=dict)
    links: Dict[str, MapLink] = field(default_factory=dict)
    scale: MapScale = field(default_factory=_default_scale)
