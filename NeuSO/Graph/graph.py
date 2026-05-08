import torch
import networkx as nx
from torch_geometric.data import Data
from Utils import load_graph, get_edge_index


class DataGraphInfo:
    def __init__(self, path):
        self.node_count = None
        self.edge_count = None
        self.directed = None
        self.v_label_count = None
        self.e_label_count = None
        self.__only_load_info(path)
        if self.e_label_count == 1:
            self.edge_labeled = False
        else:
            assert(self.e_label_count > 1)
            self.edge_labeled = True

    def __only_load_info(self, path):
        with open(path, 'r') as f:
            for line in f:
                if 'u' in line:
                    self.directed = False
                    self.node_count, self.edge_count, self.v_label_count, self.e_label_count = map(int, line.split()[1:5])
                    return
                elif 'd' in line:
                    self.directed = True
                    self.node_count, self.edge_count, self.v_label_count, self.e_label_count = map(int, line.split()[1:5])
                    return
                else:
                    print(f"line = {line}")
                    raise ValueError("Invalid graph format, must start with 'u' or 'd'")


class EncodedQueryGraph:
    def __init__(self, query_path, filter_path, data_graph_info=None, embedding_dim=32):
        if data_graph_info.edge_labeled:
            self.edge_labeled_query = True
        else:
            self.edge_labeled_query = False
        self.graph = load_graph(query_path)
        self.load_filter_info(filter_path)
        self.node_label = torch.tensor([self.graph.nodes[i]['label'] for i in range(self.graph.number_of_nodes())])
        self.edge_index = get_edge_index(self.graph)

        self.graph_data = None
        self.node_init_feature = None
        self.node_feature = None
        self.edge_feature = None
    
    def load_filter_info(self, filter_path):
        with open(filter_path) as handle:
            for line in handle:
                if not line:
                    continue
                if line[0] == "f":
                    pass
                elif line[0] == "v":
                    vertex_id, count = map(int, line.split()[1:3])
                    self.graph.nodes[vertex_id]['count'] = count
                elif line[0] == "e":
                    src, tgt, count = map(int, line.split()[1:4])
                    self.graph.edges[(src, tgt)]['count'] = count
                    if self.graph.has_edge(tgt, src):
                        self.graph.edges[(tgt, src)]['count'] = count

    def init_feature_from_label_one_hot(self, data_graph_info: DataGraphInfo):
        in_dim = data_graph_info.v_label_count
        self.node_init_feature = torch.zeros(self.node_count, in_dim)

        self.node_init_feature[torch.arange(0, self.node_count), self.node_label] = 1
        tmp = torch.log(torch.tensor([data['count'] for _, data in self.graph.nodes(data=True)]) + 1).view(-1, 1)
        self.node_init_feature = torch.cat((self.node_init_feature, tmp), dim=1)

        if self.edge_labeled_query:
            edge_one_hot = torch.nn.functional.one_hot(torch.tensor([data['label'] for _, _, data in self.graph.edges(data=True)]), num_classes=data_graph_info.e_label_count)
            edge_attr = torch.cat((self.node_init_feature[self.edge_index[0]], self.node_init_feature[self.edge_index[1]], edge_one_hot,
                                torch.log(torch.tensor([data['count'] for _, _, data in self.graph.edges(data=True)]) + 1).view(-1, 1)), dim=1)
        else:
            edge_attr = torch.cat((self.node_init_feature[self.edge_index[0]], self.node_init_feature[self.edge_index[1]],
                                torch.log(torch.tensor([data['count'] for _, _, data in self.graph.edges(data=True)]) + 1).view(-1, 1)), dim=1)

        self.graph_data = Data(x=self.node_init_feature, edge_index=self.edge_index, edge_attr=edge_attr)

    def init_feature_from_label_emb(self, label_emb_tensor, data_graph_info: DataGraphInfo):
        node_feat_tmp = label_emb_tensor[self.node_label]

        tmp = torch.log(torch.tensor([data['count'] for _, data in self.graph.nodes(data=True)]) + 1).view(-1, 1)
        self.node_init_feature = torch.cat((node_feat_tmp, tmp), dim=1)
        if self.edge_labeled_query:
            edge_one_hot = torch.nn.functional.one_hot(torch.tensor([data['label'] for _, _, data in self.graph.edges(data=True)]), num_classes=data_graph_info.e_label_count)
            edge_attr = torch.cat((node_feat_tmp[self.edge_index[0]], node_feat_tmp[self.edge_index[1]], edge_one_hot,
                                   torch.log(torch.tensor([data['count'] for _, _, data in self.graph.edges(data=True)]) + 1).view(-1, 1)), dim=1)
        else:
            edge_attr = torch.cat((node_feat_tmp[self.edge_index[0]], node_feat_tmp[self.edge_index[1]],
                                torch.log(torch.tensor([data['count'] for _, _, data in self.graph.edges(data=True)]) + 1).view(-1, 1)), dim=1)
        self.graph_data = Data(x=self.node_init_feature, edge_index=self.edge_index, edge_attr=edge_attr)

    def run_gnn(self, query_gnn):
        self.node_feature = query_gnn(self.graph_data.x, self.graph_data.edge_index)
