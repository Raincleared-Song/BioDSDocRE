import random
from torch.utils.data import Dataset
from config import task_to_config
from utils import load_json


class FineTuneDataset(Dataset):
    def __init__(self, task: str, mode: str):
        config = task_to_config[task]
        self.data = load_json(config.data_path[mode])
        if mode == 'train' or task != 'finetune' and mode == 'valid':
            # abandon those data without labels
            self.data = [item for item in self.data if len(item['labels']) != 0]
            random.shuffle(self.data)
        if mode == 'train' and task == 'finetune' and config.use_loss_weight:
            stat = load_json(config.stat_path)[config.use_stat]
            weights = [config.na_weight] * config.relation_num  # weight[id('NA')] = 1
            na_cnt = sum(stat.values())
            for key, value in stat.items():
                weights[config.label2id[key]] = na_cnt / value
            # normalize
            # total_sum = sum(weights)
            # for i in range(len(weights)):
            #     weights[i] /= total_sum
            config.loss_weight = weights

    def __getitem__(self, item):
        return self.data[item]

    def __len__(self):
        return len(self.data)
