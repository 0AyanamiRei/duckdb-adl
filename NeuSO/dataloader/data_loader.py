from pathlib import Path
import torch
from torch.utils.data import Dataset, DataLoader, random_split
from torch.nn.utils.rnn import pad_sequence
from Graph import DataGraphInfo, EncodedQueryGraph, EncodedCCG
import numpy as np
import os


class EncodedQueryDataSet(Dataset):
    def __init__(self, dataset_dir, topology_emb = False):
        self.data_graph = DataGraphInfo(dataset_dir/'data_graph' / (dataset_dir.name + '.graph'))
        label_emb_file_path = dataset_dir/'data_graph' / (dataset_dir.name + '.emb.npy')
        self.data_label_embedding = self.load_data_label(label_emb_file_path)
        self.data = self.load_data(dataset_dir, topology_emb)  # list of (encoded_query, encoded_ccg, query_name)

    def load_data(self, dataset_dir, topology_emb):
        query_dir_path = dataset_dir / 'query_graph'
        ccg_dir_path = dataset_dir / 'ccg'
        filter_dir_path = dataset_dir / 'filter'
        data = []
        for query_file_path in query_dir_path.iterdir():
            query_name = query_file_path.stem
            ccg_file_path = ccg_dir_path / (query_name + ".ccg")
            filter_file_path = filter_dir_path / (query_name + ".filter")
            if not ccg_file_path.is_file():
                continue
            encoded_query = EncodedQueryGraph(query_file_path, filter_file_path, self.data_graph)
            if topology_emb:
                encoded_query.init_feature_from_label_emb(self.data_label_embedding, self.data_graph)
            else:
                encoded_query.init_feature_from_label_one_hot(self.data_graph)
            encoded_ccg = EncodedCCG(ccg_file_path, encoded_query.graph.number_of_nodes())
            data.append((encoded_query, encoded_ccg, query_name))
        return data

    def load_data_label(self, label_emb_file_path):
        return torch.from_numpy(np.load(label_emb_file_path))

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return self.data[index]


def collect_ccw_batch(batch):   # for card, cost, wcoj data
    query_graph_batch = [item[0] for item in batch]
    ccg_batch = [item[1] for item in batch]
    query_name_batch = [item[2] for item in batch]
    return query_graph_batch, ccg_batch, query_name_batch


def collect_cons_batch(batch):  # for consistent data
    node_feature_batch = [item[0] for item in batch]
    edge_feature_batch = [item[1] for item in batch]

    node_feature_batch = torch.stack(node_feature_batch, dim=0)

    edge_feature_batch = pad_sequence(edge_feature_batch, batch_first=True, padding_value=0)
    mask = edge_feature_batch.not_equal(0).sum(dim=-1).not_equal(0)

    return node_feature_batch, edge_feature_batch, mask, torch.sum(mask).item()


class QueryDataloader(DataLoader):
    def __init__(self, dataset, batch_size, data_graph, shuffle=True):
        super().__init__(dataset=dataset, batch_size=batch_size, shuffle=shuffle, collate_fn=collect_ccw_batch, drop_last=False)
        self.data_graph = data_graph
        self.n_batches = len(self)


class WrapperDataset(Dataset):
    def __init__(self, data):
        self.data = data

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return self.data[index]


def GetDataLoader(dataset_name, query_batch_size = 3, topology_emb = False, train_data_ratio = 0.8, shuffle=True, mode = 'test', **kwargs):
    dataset_dir = Path("./dataset/") / dataset_name
    dataset = EncodedQueryDataSet(dataset_dir, topology_emb)
    train_data, test_data = random_split(dataset, [int(train_data_ratio * len(dataset)), len(dataset) - int(train_data_ratio * len(dataset))])
    train_loader = QueryDataloader(dataset=train_data, batch_size=query_batch_size, data_graph=dataset.data_graph, shuffle=shuffle)
    test_loader = QueryDataloader(dataset=test_data, batch_size=(query_batch_size if mode == 'train' else 1), data_graph=dataset.data_graph, shuffle=False)
    return train_loader, test_loader
