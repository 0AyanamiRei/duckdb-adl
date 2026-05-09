import torch
from pathlib import Path
from parser_config import ConfigParser
from dataloader.data_loader import QueryDataloader, GetDataLoader
from Trainer.trainer import Trainer
from Utils import GetModels, GetDatasetPath, SetSeed
from torch.optim.lr_scheduler import StepLR, CyclicLR
from torch.utils.tensorboard import SummaryWriter


def main(config, exp_name, checkpoint_dir):
    print(f"Experiment: {exp_name}")
    SetSeed(config['seed'])
    writer = SummaryWriter(config['tb_path']+exp_name)
    device = torch.device(config['device'])

    # Prepare dataloaders
    assert(config['mode'] == 'train')
    dataset_name = config['dataset']
    dataset_dir = GetDatasetPath(dataset_name)

    loader_cfg = config['data_loader']

    dataloader_args = {
        'dataset_name': dataset_name, **loader_cfg, 'mode': 'train'
    }
    train_loader, test_loader = GetDataLoader(**dataloader_args)

    model_cfg = config['model']
    model_list = GetModels(model_cfg)
    # Prepare optimizer
    params = (param for model in model_list for param in model.parameters())
    optimizer = torch.optim.AdamW(params, lr=config['optimizer']['lr'], weight_decay=config['optimizer']['weight_decay'])
    if config['optimizer']['scheduler'] == 'lr':
        scheduler = StepLR(optimizer, step_size=config['optimizer']['step_size'], gamma=config['optimizer']['gamma'])
    elif config['optimizer']['scheduler'] == 'cyclic':
        scheduler = CyclicLR(optimizer, base_lr=1e-5, max_lr=1e-2, step_size_up=2000, mode='triangular')
    else:
        raise (ValueError("Invalid scheduler"))

    # Prepare trainer
    trainer_cfg = {k: v for k, v in config['trainer'].items() if k != 'save_dir' and k != 'train_test_split'}
    trainer_args = {
        'model_list': model_list,
        'consistent_loss': model_cfg['consistent_loss'],
        'optimizer': optimizer, 'scheduler': scheduler, 'train_loader': train_loader, 'device': device,
        'checkpoint_dir': checkpoint_dir, "writer": writer,
        'valid_loader': test_loader
    }
    trainer_args.update(trainer_cfg)
    trainer = Trainer(**trainer_args)
    trainer.train()


if __name__ == '__main__':
    parser = ConfigParser('config.yaml')
    main(parser.config, parser.exp_name, checkpoint_dir=parser.model_save_dir)
