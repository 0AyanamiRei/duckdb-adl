import torch
import torch.nn as nn
import torch_geometric.nn as geo_nn
from Model.GNNLayer import TriATConv, CustomTransformerEncoderLayer
import scipy.sparse as sp

class GIN(nn.Module):
    def __init__(self, input_dim, hidden_dim, out_dim):
        super(GIN, self).__init__()
        nn_module_for_gin_1 = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU()
        )
        nn_module_for_gin_2 = nn.Sequential(
            nn.Linear(hidden_dim, out_dim//2),
        )
        encoder_layer = nn.TransformerEncoderLayer(d_model=out_dim // 2, nhead=4, batch_first=True)
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=1)
        self.GIN_layer_1 = geo_nn.GINConv(nn_module_for_gin_1)
        self.GIN_layer_2 = geo_nn.GINConv(nn_module_for_gin_2)

    def forward(self, in_feat, edge_list, edge_in_feat=None):
        x = self.GIN_layer_1(in_feat, edge_list)
        x = self.GIN_layer_2(x, edge_list)
        y = self.transformer_encoder(x.unsqueeze(0)).squeeze(0)
        return torch.cat((x, y), dim=1)


class GAT(nn.Module):
    def __init__(self, input_dim, hidden_dim, out_dim):
        super(GAT, self).__init__()
        self.GAT_layer_1 = geo_nn.GATConv(input_dim, hidden_dim, add_self_loops=True)
        self.GAT_layer_2 = geo_nn.GATConv(hidden_dim, out_dim//2, add_self_loops=True)
        encoder_layer = nn.TransformerEncoderLayer(d_model=hidden_dim // 2, nhead=4, batch_first=True)
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=1)


    def forward(self, in_feat, edge_list, edge_in_feat=None):
        x = self.GAT_layer_1(in_feat, edge_list)
        x = self.GAT_layer_2(x, edge_list)
        y = self.transformer_encoder(x.unsqueeze(0)).squeeze(0)
        return torch.cat((x, y), dim=1)


class TriAT(nn.Module):
    def __init__(self, input_dim, hidden_dim, out_dim, num_of_heads):
        super(TriAT, self).__init__()
        self.TriAT_layer_1 = TriATConv(v_in_dim=input_dim, e_in_dim=2*input_dim-1, out_dim=hidden_dim, num_of_heads=num_of_heads)
        self.TriAT_layer_2 = TriATConv(v_in_dim=hidden_dim, e_in_dim=hidden_dim, out_dim=out_dim, num_of_heads=num_of_heads)

        self.pool = CustomTransformerEncoderLayer(d_model=out_dim, nhead=4)

    def AddTriEdge(self, edge_index, num_nodes):
        # Create a sparse adjacency matrix
        adj = sp.coo_matrix((torch.ones(edge_index.size(1)), (edge_index[0].cpu(), edge_index[1].cpu())),
                            shape=(num_nodes, num_nodes))

        # Convert the adjacency matrix to a CSR format for fast row slicing
        adj_csr = adj.tocsr()

        # Find all triangles
        result = []
        flag = False
        for i in range(edge_index.size(1)):
            u, v = edge_index[0, i].cpu(), edge_index[1, i].cpu()
            common_neighbors = adj_csr[u].multiply(adj_csr[v]).nonzero()[1]
            for w in common_neighbors:
                result.append([w, i])  # w is the common neighbor of u and v, (u, v) is the i-th edge
                flag = True

        result = torch.tensor(result).t().to(next(self.parameters()).device)
        return result, flag

    def forward(self, node_in_feat, edge_list, edge_in_feat):
        tri_edge_index, flag = self.AddTriEdge(edge_list, node_in_feat.size(0))
        x = self.TriAT_layer_1(node_in_feat, edge_list, tri_edge_index, edge_in_feat, flag)
        edge_attr = x[edge_list[0]] + x[edge_list[1]]
        x = self.TriAT_layer_2(x, edge_list, tri_edge_index, edge_attr, flag)
        y = self.pool(x.unsqueeze(0)).squeeze(0)
        return y
