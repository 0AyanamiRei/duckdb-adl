import networkx as nx
import torch
from Graph.graph import EncodedQueryGraph

class PlanEnumerator():
    def __init__(self, state_cost_model, state_card_model, wcoj_model):
        self.state_cost_model = state_cost_model
        self.state_card_model = state_card_model
        self.wcoj_model = wcoj_model


    def GenPlan(self, query_graph: EncodedQueryGraph):
        result = []
        all_nodes = set(range(query_graph.graph.number_of_nodes()))
        while all_nodes:
            next_node = self.FindNextJoinNode(all_nodes, query_graph)
            result.append(next_node)
            all_nodes.remove(next_node)
        result.reverse()
        return result


    def GetLinkedNeighbors(self, graph, nodes):
        if len(nodes) == 1:
            return [list(nodes)[0]]
        linked_neighbors = []
        for i in nodes:
            if nx.is_connected(graph.subgraph(nodes - {i})):
                linked_neighbors.append(i)
        return linked_neighbors


    def FindNextJoinNode(self, nodes, query_graph: EncodedQueryGraph):
        if len(nodes) == 1:
            return list(nodes)[0]

        now_feature = torch.sum(query_graph.node_feature[list(nodes)], dim=0)
        linked_neighbors = self.GetLinkedNeighbors(query_graph.graph, nodes)

        child_features = now_feature - query_graph.node_feature[linked_neighbors]
        edge_features = torch.cat((child_features, now_feature.unsqueeze(0).repeat(len(linked_neighbors), 1)), dim=1)

        wcoj_op_costs = torch.exp(self.wcoj_model(edge_features))
        state_costs = torch.exp(self.state_cost_model(child_features))
        total_costs = wcoj_op_costs + state_costs

        min_cost_idx = torch.argmin(total_costs)
        best_node = linked_neighbors[min_cost_idx]

        return best_node
