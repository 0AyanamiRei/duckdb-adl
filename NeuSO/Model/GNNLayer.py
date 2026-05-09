import torch
import torch.nn as nn
import torch.nn.functional as F
import math


class TriATConv(torch.nn.Module):
    # if input_linear = True, then input x_feat.size() = (N, in_dim);
    # else x_feat.size() = (N, num_of_heads, in_dim)
    def __init__(self,  v_in_dim, e_in_dim, out_dim, num_of_heads, bias=True, concat=False, add_skip_connection=True,
                 activation=nn.ReLU()):
        super(TriATConv, self).__init__()
        self.v_in_dim = v_in_dim
        self.e_in_dim = e_in_dim
        self.out_dim = out_dim
        self.num_of_heads = num_of_heads
        self.concat = concat  # whether we should concatenate or average the attention heads
        self.add_skip_connection = add_skip_connection

        self.node_linear = torch.nn.Linear(v_in_dim, out_dim * num_of_heads)
        self.edge_linear = torch.nn.Linear(e_in_dim, out_dim * num_of_heads)

        self.leakyReLU = torch.nn.LeakyReLU(0.2)
        self.scoring_fn_target = torch.nn.Parameter(torch.Tensor(1, num_of_heads, out_dim))
        self.scoring_fn_source = torch.nn.Parameter(torch.Tensor(1, num_of_heads, out_dim))

        self.scoring_fn_edge = torch.nn.Parameter(torch.Tensor(1, num_of_heads, out_dim))
        self.scoring_fn_edge_target = torch.nn.Parameter(torch.Tensor(1, num_of_heads, out_dim))

        if bias and concat:
            self.bias = nn.Parameter(torch.Tensor(num_of_heads * out_dim))
        elif bias and not concat:
            self.bias = nn.Parameter(torch.Tensor(out_dim))
        else:
            self.register_parameter('bias', None)

        if add_skip_connection:
            self.skip_proj = nn.Linear(self.v_in_dim, num_of_heads * out_dim, bias=False)
        else:
            self.register_parameter('skip_proj', None)

        self.activation = activation

        self.init_params()

    def init_params(self):
        nn.init.xavier_uniform_(self.scoring_fn_target)
        nn.init.xavier_uniform_(self.scoring_fn_source)
        nn.init.xavier_uniform_(self.scoring_fn_edge)
        nn.init.xavier_uniform_(self.scoring_fn_edge_target)

        nn.init.kaiming_uniform_(self.node_linear.weight, a=0.2, mode='fan_in', nonlinearity='leaky_relu')
        nn.init.kaiming_uniform_(self.edge_linear.weight, a=0.2, mode='fan_in', nonlinearity='leaky_relu')

        if self.bias is not None:
            torch.nn.init.zeros_(self.bias)

    def forward(self, node_feat, edge_index, tri_edge_index, edge_feat, flag=True):

        num_of_nodes = node_feat.shape[0]

        node_feat_proj = self.node_linear(node_feat).view(-1, self.num_of_heads, self.out_dim)


        scores_source = (node_feat_proj * self.scoring_fn_source).sum(dim=-1)
        scores_target = (node_feat_proj * self.scoring_fn_target).sum(dim=-1)


        scores_source_lifted, scores_target_lifted, nodes_features_proj_lifted = self.lift_node(scores_source,
                                                                                                scores_target,
                                                                                                node_feat_proj,
                                                                                                edge_index)

        scores_per_nei_node = self.leakyReLU(scores_source_lifted + scores_target_lifted)
        attentions_per_nei = self.neighborhood_aware_softmax(scores_per_nei_node, edge_index[1], num_of_nodes)


        nodes_features_proj_lifted_weighted = nodes_features_proj_lifted * attentions_per_nei

        if flag:
            edge_feat_proj = self.edge_linear(edge_feat).view(-1, self.num_of_heads, self.out_dim)

            scores_edge = (edge_feat_proj * self.scoring_fn_edge).sum(dim=-1)
            scores_edge_target = (node_feat_proj * self.scoring_fn_edge_target).sum(dim=-1)

            scores_node_lifted, scores_tri_edge_lifted, tri_edge_features_proj_lifted = self.lift_edge(
                scores_edge_target, scores_edge, edge_feat_proj, tri_edge_index)

            scores_per_tri_edge = self.leakyReLU(scores_node_lifted + scores_tri_edge_lifted)
            attentions_per_tri_edge = self.tri_aware_softmax(scores_per_tri_edge, tri_edge_index[0], num_of_nodes)

            tri_edge_features_proj_lifted_weighted = tri_edge_features_proj_lifted * attentions_per_tri_edge

        # This part sums up weighted and projected neighborhood feature vectors for every target node
        # shape = (N, NH, FOUT)
        if flag:
            out_nodes_features = self.aggregate_neighbors(nodes_features_proj_lifted_weighted, tri_edge_features_proj_lifted_weighted, edge_index, tri_edge_index, node_feat, num_of_nodes)
        else:
            out_nodes_features = self.aggregate_neighbors(nodes_features_proj_lifted_weighted, torch.tensor(0), edge_index, tri_edge_index, node_feat, num_of_nodes)

        out_nodes_features = self.skip_concat_bias(attentions_per_nei, node_feat, out_nodes_features)


        return out_nodes_features

    def lift_node(self, scores_source, scores_target, nodes_features_matrix_proj, edge_index):
        src_nodes_index = edge_index[0]
        trg_nodes_index = edge_index[1]

        # Using index_select is faster than "normal" indexing (scores_source[src_nodes_index]) in PyTorch!
        scores_source = scores_source.index_select(0, src_nodes_index)
        scores_target = scores_target.index_select(0, trg_nodes_index)
        nodes_features_matrix_proj_lifted = nodes_features_matrix_proj.index_select(0, src_nodes_index)

        return scores_source, scores_target, nodes_features_matrix_proj_lifted

    def lift_edge(self, scores_target, scores_edge, edges_features_matrix_proj, tri_edge_index):
        node_index = tri_edge_index[0]
        edge_index = tri_edge_index[1]

        scores_node = scores_target.index_select(0, node_index)
        scores_edge = scores_edge.index_select(0, edge_index)
        edges_features_matrix_proj_lifted = edges_features_matrix_proj.index_select(0, edge_index)

        return scores_node, scores_edge, edges_features_matrix_proj_lifted

    def neighborhood_aware_softmax(self, scores_per_edge, trg_index, num_of_nodes):
        # Calculate the numerator. Make logits <= 0 so that e^logit <= 1 (this will improve the numerical stability)
        scores_per_edge = scores_per_edge - scores_per_edge.max()

        # Calculate the denominator. shape = (E, NH)
        neigborhood_aware_denominator = self.sum_edge_scores_neighborhood_aware(scores_per_edge, trg_index, num_of_nodes)

        # 1e-16 is theoretically not needed but is only there for numerical stability (avoid div by 0) - due to the
        # possibility of the computer rounding a very small number all the way to 0.
        attentions_per_edge = scores_per_edge / (neigborhood_aware_denominator + 1e-16)

        # shape = (E, NH) -> (E, NH, 1) so that we can do element-wise multiplication with projected node features
        return attentions_per_edge.unsqueeze(-1)

    def tri_aware_softmax(self, scores_per_edge, trg_index, num_of_nodes):
        scores_per_edge = scores_per_edge - scores_per_edge.max()
        exp_scores_per_edge = scores_per_edge.exp()
        neigborhood_aware_denominator = self.sum_edge_scores_neighborhood_aware(exp_scores_per_edge, trg_index,
                                                                                num_of_nodes)
        attentions_per_edge = exp_scores_per_edge / (neigborhood_aware_denominator + 1e-16)
        return attentions_per_edge.unsqueeze(-1)

    def sum_edge_scores_neighborhood_aware(self, exp_scores_per_edge, trg_index, num_of_nodes):
        # The shape must be the same as in exp_scores_per_edge (required by scatter_add_) i.e. from E -> (E, NH)
        trg_index_broadcasted = self.explicit_broadcast(trg_index, exp_scores_per_edge)

        # shape = (N, NH), where N is the number of nodes and NH the number of attention heads
        size = list(exp_scores_per_edge.shape)  # convert to list otherwise assignment is not possible
        size[0] = num_of_nodes
        neighborhood_sums = torch.zeros(size, dtype=exp_scores_per_edge.dtype, device=exp_scores_per_edge.device)

        # position i will contain a sum of exp scores of all the nodes that point to the node i (as dictated by the
        # target index)
        neighborhood_sums.scatter_add_(0, trg_index_broadcasted, exp_scores_per_edge)

        # Expand again so that we can use it as a softmax denominator. e.g. node i's sum will be copied to
        # all the locations where the source nodes pointed to i (as dictated by the target index)
        # shape = (N, NH) -> (E, NH)
        return neighborhood_sums.index_select(0, trg_index)

    def aggregate_neighbors(self, nodes_features_proj_lifted_weighted, tri_edge_features_proj_lifted_weighted, edge_index, tri_edge_index, in_nodes_features, num_of_nodes):
        size = list(nodes_features_proj_lifted_weighted.shape)  # convert to list otherwise assignment is not possible
        size[0] = num_of_nodes  # shape = (N, NH, FOUT)
        out_nodes_features = torch.zeros(size, dtype=in_nodes_features.dtype, device=in_nodes_features.device)

        # shape = (E) -> (E, NH, FOUT)
        # aggregation step - we accumulate projected, weighted node features for all the attention heads
        # shape = (E, NH, FOUT) -> (N, NH, FOUT)
        trg_index_broadcasted = self.explicit_broadcast(edge_index[1], nodes_features_proj_lifted_weighted)
        out_nodes_features.scatter_add_(0, trg_index_broadcasted, nodes_features_proj_lifted_weighted)

        if len(tri_edge_index):
            tri_edge_index_broadcasted = self.explicit_broadcast(tri_edge_index[0], tri_edge_features_proj_lifted_weighted)
            out_nodes_features.scatter_add_(0, tri_edge_index_broadcasted, tri_edge_features_proj_lifted_weighted)

        return out_nodes_features

    def explicit_broadcast(self, this, other):
        # Append singleton dimensions until this.dim() == other.dim()
        for _ in range(this.dim(), other.dim()):
            this = this.unsqueeze(-1)

        # Explicitly expand so that shapes are the same
        return this.expand_as(other)

    def skip_concat_bias(self, attention_coefficients, in_nodes_features, out_nodes_features):

        # if the tensor is not contiguously stored in memory we'll get an error after we try to do certain ops like view
        # only imp1 will enter this one
        if not out_nodes_features.is_contiguous():
            out_nodes_features = out_nodes_features.contiguous()

        if self.add_skip_connection:  # add skip or residual connection
            if out_nodes_features.shape[-1] == in_nodes_features.shape[-1]:  # if FIN == FOUT
                # unsqueeze does this: (N, FIN) -> (N, 1, FIN), out features are (N, NH, FOUT) so 1 gets broadcast to NH
                # thus we're basically copying input vectors NH times and adding to processed vectors
                out_nodes_features += in_nodes_features.unsqueeze(1)
            else:
                # FIN != FOUT so we need to project input feature vectors into dimension that can be added to output
                # feature vectors. skip_proj adds lots of additional capacity which may cause overfitting.
                out_nodes_features += self.skip_proj(in_nodes_features).view(-1, self.num_of_heads, self.out_dim)

        if self.concat:
            # shape = (N, NH, FOUT) -> (N, NH*FOUT)
            out_nodes_features = out_nodes_features.view(-1, self.num_of_heads * self.out_dim)
        else:
            # shape = (N, NH, FOUT) -> (N, FOUT)
            out_nodes_features = out_nodes_features.mean(dim=1)

        if self.bias is not None:
            out_nodes_features += self.bias

        return out_nodes_features if self.activation is None else self.activation(out_nodes_features)


class CustomTransformerEncoderLayer(nn.Module):
    def __init__(self, d_model, nhead):
        super(CustomTransformerEncoderLayer, self).__init__()
        self.d_model = d_model
        self.nhead = nhead
        self.head_dim = d_model // nhead

        # One big projection for Q, K, V
        self.qkv_proj = nn.Linear(d_model, d_model * 3)
        self.out_proj = nn.Linear(d_model, d_model)

        self.init_parameters()

    def init_parameters(self):
        nn.init.xavier_uniform_(self.qkv_proj.weight)
        nn.init.xavier_uniform_(self.out_proj.weight)

    def forward(self, query, attn_mask=None, key_padding_mask=None):
        batch_size, seq_len, _ = query.size()

        # Concatenate Q, K, V projection in one step
        qkv = self.qkv_proj(query)   # [B, L, 3*d_model]
        Q, K, V = qkv.chunk(3, dim=-1)   # split into three parts

        # Reshape for multi-head
        Q = Q.view(batch_size, seq_len, self.nhead, self.head_dim).transpose(1, 2)
        K = K.view(batch_size, seq_len, self.nhead, self.head_dim).transpose(1, 2)
        V = V.view(batch_size, seq_len, self.nhead, self.head_dim).transpose(1, 2)

        # Attention calculation
        attn_scores = torch.matmul(Q, K.transpose(-2, -1)) / math.sqrt(self.head_dim)
        if attn_mask is not None:
            attn_scores = attn_scores.masked_fill(attn_mask == 0, float('-inf'))
        if key_padding_mask is not None:
            attn_scores = attn_scores.masked_fill(key_padding_mask.unsqueeze(1).unsqueeze(2), float('-inf'))

        attn_weights = F.softmax(attn_scores, dim=-1)
        attn_output = torch.matmul(attn_weights, V)

        # Concatenate heads
        attn_output = attn_output.transpose(1, 2).contiguous().view(batch_size, seq_len, self.d_model)

        return self.out_proj(attn_output)
