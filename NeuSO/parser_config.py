from pathlib import Path
from datetime import datetime
from Logger import setup_logging
from Utils import read_yaml, write_yaml

class ConfigParser:
    def __init__(self, config_path, run_id=None, test_mode = False):
        self.config = read_yaml(config_path)
        self.config['name'] = self.config['model']['query_gnn']['model'] + "_" + \
                ('topology' if self.config['data_loader']['topology_emb'] else 'onehot')

        self.exp_name = self.config['dataset'] + '_' + str(datetime.now().strftime("%Y-%m-%d-%H-%M"))
        self.save_dir = Path("./saved/" + self.exp_name)
        self.model_save_dir = self.save_dir / 'model'

        self.config_model_param()

        if self.config['mode'] == 'train' and not test_mode:
            self.model_save_dir.mkdir(parents=True, exist_ok=True)
            write_yaml(self.config, self.save_dir / 'config.yaml')
            setup_logging(self.save_dir)
        else:
            self.save_dir = None

    def config_model_param(self):
        dataset_indim_map = {
            'yeast': 71,
            'hprd': 307,
            'dblp': 15,
            'eu2005': 40,
            'youtube': 25,
            'patents': 20
        }
        # + 1 for the candidate size
        if ('topology_emb' in self.config['data_loader'] and self.config['data_loader']['topology_emb']):
            self.config['model']['query_gnn']['in_dim'] = self.config['data_loader']['topology_emb_dim'] + 1
        else:
            self.config['model']['query_gnn']['in_dim'] = dataset_indim_map[self.config['data_loader']['dataset']] + 1

        self.config['model']['wcoj_cost_model']['in_dim'] = 2*int(self.config['model']['query_gnn']['out_dim'])
