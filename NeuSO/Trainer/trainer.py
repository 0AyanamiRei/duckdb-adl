import numpy as np
import torch
import logging
from dataloader.data_loader import WrapperDataset, collect_cons_batch
from torch.utils.data import DataLoader
from itertools import zip_longest
from Graph import EncodedCCG
from Model.loss import log_loss
import time
from Trainer.TrainTest import TrainTest


class Trainer(TrainTest):
    def __init__(self, model_list, consistent_loss,
                 batch_size, loss_coeff, optimizer, scheduler, train_loader, epochs, device, checkpoint_dir, save_period=1, valid_loader=None, writer=None):
        super(Trainer, self).__init__(model_list, device=device)

        self.consistent_loss = (consistent_loss == 'add')

        self.batch_size = batch_size
        self.loss_coeff = [loss_coeff["cost_coeff"], loss_coeff["card_coeff"], loss_coeff["wcoj_coeff"]]

        self.epochs = epochs
        self.optimizer = optimizer
        self.scheduler = scheduler

        self.train_loader = train_loader
        self.valid_loader = valid_loader

        self.prefixes = ['cost', 'card', 'wcoj']

        self.logger = logging.getLogger('Trainer')
        self.logger.setLevel(logging.DEBUG)

        self.save_period = save_period
        self.checkpoint_dir = checkpoint_dir
        if writer is not None:
            self.writer = writer
        else:
            self.writer = None


    def calculate_mini_batch(self, mini_batch, model, loss_cof):
        embed, target = mini_batch[0].to(self.device), mini_batch[1].to(self.device)
        predict_log = model(embed).view(-1)
        loss_cur = log_loss(predict_log, target)  # default log_loss, the predict is log values
        return loss_cur * loss_cof


    def get_batch_train_dataset(self, encoded_queries, encode_ccgs):
        cost_data, card_data, wcoj_data = [], [], []
        consistent_data = []
        for encoded_query, encoded_ccg in zip(encoded_queries, encode_ccgs):
            x, edge_index, edge_attr, ccg_edge_cost = (encoded_query.graph_data.x.to(self.device),
                                                                    encoded_query.graph_data.edge_index.to(self.device),
                                                                    encoded_query.graph_data.edge_attr.to(self.device),
                                                                    encoded_ccg.edge_cost)
            node_feature = self.query_model(x, edge_index, edge_attr)

            encoded_ccg.init_feature(node_feature)
            ccg_node_feature = encoded_ccg.ccg_node_feature.to(self.device)
            ccg_edge_feature = encoded_ccg.ccg_edge_feature.to(self.device)
            ccg_edge_cost = encoded_ccg.edge_cost.to(self.device)

            cost_data.extend([(feature, vertex.best_cost) for feature, vertex in zip(ccg_node_feature[1:], encoded_ccg.vertices[1:])] if encoded_ccg.total_exploration else [])
            card_data.extend([(feature, vertex.cardinality) for feature, vertex in zip(ccg_node_feature[1:], encoded_ccg.vertices[1:])])
            wcoj_data.extend([(feature, edge_cost) for feature, edge_cost in zip(ccg_edge_feature, ccg_edge_cost)])
            consistent_data.extend([(feature_node, feature_edge) for feature_node, feature_edge in zip(ccg_node_feature[1:], encoded_ccg.ccg_consistent_feature)])

        cost_dataset, card_dataset, wcoj_dataset, consistent_dataset = map(WrapperDataset, (cost_data, card_data, wcoj_data, consistent_data))

        cost_loader = DataLoader(cost_dataset, batch_size=self.batch_size, shuffle=True) if len(cost_data) > 0 else iter([])
        card_loader = DataLoader(card_dataset, batch_size=self.batch_size, shuffle=True)
        wcoj_loader = DataLoader(wcoj_dataset, batch_size=self.batch_size, shuffle=True)
        consistent_loader = DataLoader(consistent_dataset, batch_size=self.batch_size , shuffle=True, collate_fn=collect_cons_batch)

        return cost_loader, card_loader, wcoj_loader, consistent_loader


    def _train_epoch(self, epoch):
        self.model_train()
        epoch_loss = 0.0
        mini_batch_loss = 0.0

        for batch_idx, (encoded_queries, encoded_ccgs, name) in enumerate(self.train_loader):
            cost_loader, card_loader, wcoj_loader, consistent_loader =  self.get_batch_train_dataset(encoded_queries, encoded_ccgs)

            loss = 0.0
            for cost_batch, card_batch, wcoj_batch in zip_longest(cost_loader, card_loader, wcoj_loader):
                for prefix, predict_model, data_batch, coefficient in zip(self.prefixes, self.models[1:],
                                                             [cost_batch, card_batch, wcoj_batch], self.loss_coeff):
                    if data_batch is not None:
                        loss_cur = self.calculate_mini_batch(data_batch, predict_model, coefficient)
                        loss += loss_cur

            if self.consistent_loss:
                for consistent_batch in consistent_loader:
                    consistent_node_feature = consistent_batch[0].to(self.device)
                    consistent_edge_feature = consistent_batch[1].to(self.device)
                    mask = consistent_batch[2].to(self.device)

                    min_cost_est = self.models[1](consistent_node_feature).squeeze()
                    wcoj_est = self.models[3](consistent_edge_feature).squeeze()
                    min_wcoj_est = torch.min(torch.where(mask, wcoj_est, torch.inf), dim=1).values

                    loss_cur = torch.norm(torch.max(torch.tensor([0.0]).to(self.device), (min_wcoj_est - min_cost_est))) **2 / consistent_batch[3]
                    loss += loss_cur

            epoch_loss += loss
            mini_batch_loss += loss
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            if torch.isnan(loss):
                self.logger.info(f"ERROR, loss is nan")
                exit(-1)

            if batch_idx % 10 == 0 or batch_idx == self.train_loader.n_batches - 1:
                self.logger.info('Train Epoch: {} {}, mini_batch loss: {}'.format(epoch, self._progress(batch_idx + 1), mini_batch_loss.item()))
                mini_batch_loss = 0.0

        return {
            'epoch_loss': epoch_loss.item(),
            'lr': self.optimizer.param_groups[0]["lr"],
            'epoch': epoch
        }


    def _valid_epoch(self, epoch):
        self.model_eval()
        epoch_loss = 0.0
        mini_batch_loss = 0.0

        with torch.no_grad():

            for batch_idx, (encoded_queries, encoded_ccgs, name) in enumerate(self.valid_loader):
                cost_loader, card_loader, wcoj_loader, consistent_loader = self.get_batch_train_dataset(encoded_queries, encoded_ccgs)

                loss = 0.0
                for cost_batch, card_batch, wcoj_batch in zip_longest(cost_loader, card_loader, wcoj_loader):
                    for prefix, predict_model, data_batch, coefficient in zip(self.prefixes, self.models[1:],
                                                                                        [cost_batch, card_batch, wcoj_batch], self.loss_coeff):
                        if data_batch is not None:
                            loss_cur = self.calculate_mini_batch(data_batch, predict_model, coefficient)
                            loss += loss_cur

                epoch_loss += loss
                mini_batch_loss += loss

                if batch_idx % 10 == 0:
                    self.logger.info('Valid Epoch: {}, mini_batch loss: {}'.format(epoch,mini_batch_loss.item()))
                    mini_batch_loss = 0.0

        return epoch_loss.item()


    def train(self):
        best_valid_loss = float('inf')
        for epoch in range(self.epochs):
            result = self._train_epoch(epoch)
            if self.writer is not None:
                self.writer.add_scalar('train_epoch_loss/epoch', result['epoch_loss'], epoch)
                self.writer.add_scalar('lr/epoch', self.optimizer.param_groups[0]["lr"], epoch)
            self.scheduler.step()

            if self.valid_loader is not None:
                valid_loss = self._valid_epoch(epoch)
                result.update({"val_loss":valid_loss})
                if valid_loss < best_valid_loss:
                    best_valid_loss = valid_loss
                    self._save_best_model()
                    self.logger.info('Best model saved at epoch {} with validation loss: {:.4f}'.format(epoch, best_valid_loss))

                if self.writer is not None:
                    self.writer.add_scalar('valid_epoch_loss/epoch', valid_loss, epoch)

            self.logger.info('Epoch {} finished.'.format(epoch))
            for key, value in result.items():
                self.logger.info('    {:35s}: {}'.format(str(key), value))

            if epoch % self.save_period == 0:
                self._save_checkpoint(epoch)
    

    def _save_best_model(self):
        filename = str(self.checkpoint_dir / 'best.pth')
        self.save_models(filename)
        self.logger.info("Saving best model: {} ...".format(filename))


    def _save_checkpoint(self, epoch):
        filename = str(self.checkpoint_dir / 'checkpoint-epoch{}.pth'.format(epoch))
        self.save_models(filename)
        self.logger.info("Saving checkpoint: {} ...".format(filename))


    def _resume_checkpoint(self, resume_path):
        resume_path = str(resume_path)
        self.logger.info("Loading checkpoint: {} ...".format(resume_path))
        super().resume_models(resume_path)


    def _load_best_model(self):
        best_model_path = str(self.checkpoint_dir / 'best.pth')
        super().resume_models(best_model_path)


    def _progress(self, batch_idx):
        base = '[{}/{} ({:.0f}%)]'
        current = batch_idx
        total = self.train_loader.n_batches
        return base.format(current, total, 100.0 * current / total)
