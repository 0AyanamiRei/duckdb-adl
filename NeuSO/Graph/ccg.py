import time
import torch


class CCGVertex:
    def __init__(self, ccg_id: int, subquery: list):
        self.id = ccg_id
        self.subquery = subquery

        self.cardinality = None
        self.best_cost = None

        self.wcoj_children = []
        self.wcoj_cost = []

    def add_wcoj(self, child, cost):
        self.wcoj_children.append(child)
        self.wcoj_cost.append(cost)

    def set_cardinality(self, cardinality):
        self.cardinality = cardinality

    def set_best_cost(self, best_cost):
        self.best_cost = best_cost


class CCG:
    def __init__(self, path, query_node_count, ignore_card_cost=False):
        self.vertices_count = 0
        self.wj_edges_count = 0

        self.vertices = []  # a list of CCGVertex
        self.edges = []  # a list of [small_id, big_id, join_node, cost]
        self.subquery_map = [] # a list of list, map from ccg_node_id to its subquery node id
        self.root = None
        self.total_exploration = False
        self.final_state_exploration = False
        self.__load_ccg(path, query_node_count, ignore_card_cost)

    def __load_ccg(self, path, query_node_count, ignore_card_cost: bool):
        with open(path, 'r') as f:
            invalid = int(0xffffffffffffffff)
            for line in f:
                parts = line.strip().split()
                if len(parts) == 0:
                    break
                if parts[0] == 't' or parts[0] == 'p':
                    self.vertices_count, self.wj_edges_count = map(int, parts[1:])
                    self.vertices = [None] * self.vertices_count
                    self.total_exploration = parts[0] == 't'
                    self.subquery_map = [[] for _ in range(self.vertices_count)]

                elif parts[0] == 'v':
                    ccg_vertex_id, card, min_cost, subquery_size = map(int, parts[1: 5])
                    subquery = list(map(int, parts[5:5+subquery_size]))
                    ccg_vertex = CCGVertex(ccg_vertex_id, subquery)
                    if not ignore_card_cost:
                        if card != invalid:
                            ccg_vertex.set_cardinality(card)
                        if min_cost != invalid:
                            ccg_vertex.set_best_cost(min_cost)
                    self.vertices[ccg_vertex_id] = ccg_vertex
                    if subquery_size == query_node_count:
                        self.final_state_exploration = True

                elif parts[0] == 'w':
                    small_id, big_id, join_node, cost = map(int, parts[1:5])
                    cost = None if ignore_card_cost or cost == invalid else cost
                    self.vertices[big_id].add_wcoj(self.vertices[small_id], cost)
                    self.edges.append((small_id, big_id, join_node, cost))
                    self.subquery_map[big_id].append(small_id)  # subquery, not subquery nodes

            self.root = None if len(self.vertices) == 0 else self.vertices[-1]


class EncodedCCG(CCG):
    def __init__(self, path, query_node_count, ignore_card_cost=False):
        super().__init__(path, query_node_count, ignore_card_cost)
        self.ccg_node_feature = None  # ccg node init feature
        self.ccg_edge_feature = None # ccg edge init feature
        self.whole_query_feature = None

        self.edge_index = torch.tensor([[edge[0], edge[1]] for edge in self.edges if edge[3] is not None]).t()
        self.edge_cost = torch.tensor([edge[3] for edge in self.edges if edge[3] is not None], dtype=torch.float32)

    def init_feature(self, node_features: torch.tensor):
        init_feature_list = [torch.sum(node_features[ccg_vertex.subquery], dim=0) for ccg_vertex in self.vertices]
        self.whole_query_feature = torch.sum(node_features, dim=0)
        self.ccg_node_feature = torch.stack(init_feature_list)

        self.ccg_edge_feature = [torch.cat((self.ccg_node_feature[edge[0]], self.ccg_node_feature[edge[1]]), dim = 0) for edge in self.edges]
        self.ccg_edge_feature = torch.stack(self.ccg_edge_feature)

        self.ccg_consistent_feature = [torch.stack( [torch.cat((self.ccg_node_feature[x], self.ccg_node_feature[ccg_vertex.id]), dim = 0) for x in self.subquery_map[ccg_vertex.id]] ) for ccg_vertex in self.vertices[1:]]