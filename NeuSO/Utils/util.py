import yaml
import torch
import torch_geometric as tg
import numpy as np
import networkx as nx
from pathlib import Path
from Model.GNN import GIN, GAT, TriAT
from Model.PredictModel import MLP
import random
import os


def read_yaml(file) -> dict:
    file = Path(file)
    with file.open('rt') as handle:
        config = yaml.safe_load(handle)
    return config


def write_yaml(content, file):
    file = Path(file)
    with file.open('wt') as handle:
        yaml.dump(content, handle, indent=2)


def load_graph(file):
    g = nx.DiGraph()
    vertices, edges = [], []
    directed = False
    with open(file) as handle:
        for line in handle:
            if not line:
                continue
            if line[0] == "d":
                directed = True
            elif line[0] == "v":
                vertex_id, label = map(int, line.split()[1:3])
                vertices.append((vertex_id, {"label": label}))
            elif line[0] == "e":
                src, tgt, e_label = map(int, line.split()[1:4])
                edges.append((src, tgt, {"label": e_label}))
                if not directed:
                    edges.append((tgt, src, {"label": e_label}))
    g.add_nodes_from(vertices)
    g.add_edges_from(edges)
    return g


def get_query_size(query_name: str):
    query_size_list = range(1, 40)
    for size in query_size_list:
        if f"query_{size}_" in query_name:
            return size
        elif f"query_dense_{size}_" in query_name or f"query_sparse_{size}_" in query_name:
            return size
    raise ValueError(f"Query size not found in query name: {query_name}")

def load_ground_truth(file):  # load ground truth file for cardinality estimation
    ground_truth = {}
    with open(file) as handle:
        for line in handle:
            if not line:
                continue
            query_name, count = line.split()
            ground_truth[query_name] = int(count)
    return ground_truth


def get_edge_index(graph: nx.DiGraph):
    return torch.tensor(list(graph.edges()), dtype=torch.long).t().contiguous()


def log_transform(tensor):
    return torch.where(tensor < 1.0, torch.log(tensor + 1), torch.log(torch.log(tensor) + 2))


def reverse_log_transform(tensor):
    mid = torch.log(torch.tensor(2.0))
    return torch.where(tensor < mid, torch.exp(mid) - 1, torch.exp(torch.exp(tensor) - 2))

def q_error(pred, targ):
    pred += 0.0001
    targ += 0.0001
    return max(pred/targ, targ/pred)

def GetModels(model_cfg):
    if model_cfg['query_gnn']['model'] == 'GIN':
        query_graph_model = GIN(model_cfg["query_gnn"]["in_dim"], model_cfg["query_gnn"]["hid_dim"], model_cfg["query_gnn"]["out_dim"])
    elif model_cfg['query_gnn']['model'] == 'GAT':
        query_graph_model = GAT(model_cfg["query_gnn"]["in_dim"], model_cfg["query_gnn"]["hid_dim"], model_cfg["query_gnn"]["out_dim"])
    elif model_cfg['query_gnn']['model'] == 'TriAT':
        query_graph_model = TriAT(model_cfg["query_gnn"]["in_dim"], model_cfg["query_gnn"]["hid_dim"], model_cfg["query_gnn"]["out_dim"], model_cfg["query_gnn"]["head_num"])
    else:
        raise ValueError(f"Unsupported query GNN model: {model_cfg['query_gnn']['model']}")

    state_cost_model = MLP(model_cfg["state_cost_model"]["in_dim"], model_cfg["state_cost_model"]["hid_dim"],
                           model_cfg["state_cost_model"]["out_dim"])
    state_card_model = MLP(model_cfg["state_card_model"]["in_dim"], model_cfg["state_card_model"]["hid_dim"],
                           model_cfg["state_card_model"]["out_dim"])
    wcoj_cost_model = MLP(model_cfg["wcoj_cost_model"]["in_dim"], model_cfg["wcoj_cost_model"]["hid_dim"],
                          model_cfg["wcoj_cost_model"]["out_dim"])

    return query_graph_model, state_cost_model, state_card_model, wcoj_cost_model

def GetDatasetPath(dataset_name):
    print(f"Dataset name from config: {dataset_name}")
    dataset_dir = Path("./dataset/") / dataset_name
    print(f"Using dataset: {dataset_dir}")
    return dataset_dir

def SetSeed(seed):
    os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':4096:8'
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    tg.seed.seed_everything(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True)