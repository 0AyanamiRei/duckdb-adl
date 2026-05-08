import numpy as np
import torch
import logging
from Model.loss import mse_loss
from dataloader.data_loader import WrapperDataset
from torch.utils.data import DataLoader
from itertools import zip_longest
from Graph import EncodedCCG
import time
from Plan.plan_enumerator import PlanEnumerator
from Trainer.TrainTest import TrainTest


class Tester(TrainTest):
    def __init__(self, model_list,
                 data_loader, device, resume_path=None):
        super(Tester, self).__init__(model_list, device)

        self.data_loader = data_loader
        self.resume_models(resume_path)

    def warm_up_models(self):
        self.model_eval()
        index = 0
        with torch.no_grad():
            for batch_idx, (encoded_query, encoded_ccg, name) in enumerate(self.data_loader):
                encoded_query, name = encoded_query[0], name[0]
                x, edge_index, edge_attr, edge_weight = (encoded_query.graph_data.x.to(self.device),
                                                         encoded_query.graph_data.edge_index.to(self.device),
                                                         encoded_query.graph_data.edge_attr.to(self.device),
                                                         encoded_query.filter_info.edge_weight.to(self.device))

                node_feature = self.query_model(x, edge_index, edge_attr, edge_weight)
                query_embedding = node_feature.sum(dim=0).unsqueeze(0)
                predict_log = self.state_card_model(query_embedding)
                predict_log = self.state_cost_model(query_embedding)
                predict_log = self.wcoj_cost_model(torch.cat([query_embedding, query_embedding], dim = 1))

                index += 1
                if index == 5:
                    break


    def test_cardinality(self):  # only test cardinality
        self.warm_up_models()
        self.model_eval()
        results = []
        with torch.no_grad():
            for _, (encoded_query, encoded_ccg, name) in enumerate(self.data_loader):
                encoded_query, encoded_ccg, name = encoded_query[0], encoded_ccg[0], name[0]
                x, edge_index, edge_attr = (encoded_query.graph_data.x.to(self.device),
                                            encoded_query.graph_data.edge_index.to(self.device),
                                            encoded_query.graph_data.edge_attr.to(self.device))
                start_time = time.time()
                node_feature = self.query_model(x, edge_index, edge_attr)
                query_embedding = node_feature.sum(dim=0).unsqueeze(0)
                predict_log = self.state_card_model(query_embedding)
                end_time = time.time()
                results.append((name, torch.exp(predict_log).item(), (end_time-start_time)*1000))
        return results
    

    def generate_plans(self):
        self.warm_up_models()
        self.model_eval()
        results = []
        with torch.no_grad():
            for _, (encoded_query, encoded_ccg, name) in enumerate(self.data_loader):
                encoded_query, name = encoded_query[0], name[0]
                x, edge_index, edge_attr = (
                    encoded_query.graph_data.x.to(self.device),
                    encoded_query.graph_data.edge_index.to(self.device),
                    encoded_query.graph_data.edge_attr.to(self.device))
                start_time = time.time()
                node_feature = self.query_model(x, edge_index, edge_attr)
                query_plan = self.plan_enumerator.GenPlan(encoded_query, node_feature)
                end_time = time.time()
                results.append((name, query_plan, (end_time - start_time) * 1000))
        return results
