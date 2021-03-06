from config import ConfigFineTune as Config
from utils import load_json
import numpy as np
import random
import os
import torch


use_score = Config.score_path is not None and os.path.exists(Config.score_path['train']) \
    and os.path.exists(Config.score_path['valid']) and os.path.exists(Config.score_path['test'])
if use_score:
    mode_to_scores = {
        'train': np.load(Config.score_path['train']),
        'valid': np.load(Config.score_path['valid']),
        'test': np.load(Config.score_path['test'])
    }
    title_list = {
        'train': load_json(Config.title_path['train']),
        'valid': load_json(Config.title_path['valid']),
        'test': load_json(Config.title_path['test'])
    }
    mode_to_titles = {
        'train': {title_list['train'][i]: i for i in range(len(title_list['train']))},
        'valid': {title_list['valid'][i]: i for i in range(len(title_list['valid']))},
        'test': {title_list['test'][i]: i for i in range(len(title_list['test']))}
    }


def process_finetune(data, mode: str):
    documents, labels, head_poses, tail_poses, label_masks, attn_masks, pairs_list, titles, types = \
        [], [], [], [], [], [], [], [doc['title'] for doc in data], []
    for doc in data:
        if mode == 'test' and 'labels' in doc:
            del doc['labels']
        document, label, head_pos, tail_pos, label_mask, attn_mask, pair_ids, typ = process_single(doc, mode)
        documents.append(document)
        labels.append(label)
        head_poses.append(head_pos)
        tail_poses.append(tail_pos)
        label_masks.append(label_mask)
        attn_masks.append(attn_mask)
        pairs_list.append(pair_ids)
        types.append(typ)
    return {
        'documents': torch.LongTensor(documents),
        'labels': torch.LongTensor(labels),
        'head_pos': torch.LongTensor(head_poses),
        'tail_pos': torch.LongTensor(tail_poses),
        'label_mask': torch.LongTensor(label_masks),
        'attn_mask': torch.FloatTensor(attn_masks),
        'pair_ids': pairs_list,
        'titles': titles,
        'types': types
    }


def process_single(data, mode: str):
    global mode_to_scores, mode_to_titles, use_score
    entities = data['vertexSet']
    entity_num = len(entities)
    sentences = [[Config.tokenizer.tokenize(word) for word in sent] for sent in data['sents']]
    reserved_pairs = []
    if use_score:
        titles = mode_to_titles[mode]
        # noinspection PyUnresolvedReferences
        scores = mode_to_scores[mode][titles[data['title']]]
        pair_scores = []
        for i in range(entity_num):
            for j in range(entity_num):
                if i == j:
                    continue
                pair_scores.append(((i, j), scores[len(pair_scores)]))
        pair_scores.sort(reverse=True, key=lambda x: x[1])

        reserve_num = min(entity_num << 1, Config.kept_pair_num)
        entity_set = set()
        # reserve pairs with highest score
        reserved_pairs = pair_scores[:reserve_num]
        for pair in reserved_pairs:
            entity_set.add(pair[0][0])
            entity_set.add(pair[0][1])

        entity_set = sorted(list(entity_set))

        for i, eid in enumerate(entity_set):
            for mention in entities[eid]:
                sentences[mention['sent_id']][mention['pos'][0]].insert(0, f'[unused{(i << 1) + 1}]')
                sentences[mention['sent_id']][mention['pos'][1] - 1].append(f'[unused{(i + 1) << 1}]')

    else:
        for i, mentions in enumerate(entities):
            for mention in mentions:
                sentences[mention['sent_id']][mention['pos'][0]].insert(0, f'[unused{(i << 1) + 1}]')
                sentences[mention['sent_id']][mention['pos'][1] - 1].append(f'[unused{(i + 1) << 1}]')

    word_position, document = [], ['[CLS]']
    for sent in sentences:
        word_position.append([])
        for word in sent:
            word_position[-1].append(len(document))
            document += word
    word_position.append([len(document)])

    # pad each document
    if len(document) < Config.token_padding:
        document.append('[SEP]')
        document += ['[PAD]'] * (Config.token_padding - len(document))
        attn_mask = [1] * len(document) + [0] * (Config.token_padding - len(document))
    else:
        document = document[:(Config.token_padding - 1)] + ['[SEP]']
        attn_mask = [1] * Config.token_padding

    positions = []
    for entity in entities:
        cur_entity = []
        for mention in entity:
            if word_position[mention['sent_id']][mention['pos'][0]] < Config.token_padding:
                cur_entity.append(word_position[mention['sent_id']][mention['pos'][0]])
            if len(cur_entity) == Config.mention_padding:
                break
        positions.append(cur_entity)
    # padding length of mention number to 3
    for i in range(len(positions)):
        if len(positions[i]) == 0:
            positions[i] = [0] * Config.mention_padding
        positions[i] += [positions[i][0]] * (Config.mention_padding - len(positions[i]))

    label_mat = np.zeros((entity_num, entity_num, Config.relation_num))
    label_mat[:, :, Config.label2id['NA']] = 1
    positive_pairs = set()
    if 'labels' in data:
        for lab in data['labels']:
            positive_pairs.add((lab['h'], lab['t']))
            label_mat[lab['h'], lab['t'], Config.label2id[lab['r']]] = 1
            label_mat[lab['h'], lab['t'], Config.label2id['NA']] = 0
    negative_pairs = [(i, j) for i in range(entity_num) for j in range(entity_num) if i != j
                      and (i, j) not in positive_pairs]

    head_pos, tail_pos, labels, pair_ids, types = [], [], [], [], []

    if use_score:
        sample_limit = Config.score_sample_limit
        samples = [pair[0] for pair in reserved_pairs]
    elif mode == 'train':
        sample_limit = Config.train_sample_limit
        samples = list(positive_pairs) + random.sample(negative_pairs, min(len(positive_pairs) * 3,
                                                                           sample_limit - len(positive_pairs),
                                                                           len(negative_pairs)))
    else:
        sample_limit = Config.test_sample_limit
        samples = list(positive_pairs) + negative_pairs

    for pair in samples:
        pair_ids.append(pair)
        head_pos.append(positions[pair[0]])
        tail_pos.append(positions[pair[1]])
        labels.append(label_mat[pair[0], pair[1]])  # [[97], [97]]

        if Config.use_entity_type:
            types.append((entities[pair[0]][0]['type'], entities[pair[1]][0]['type']))

    # pad labels to sample_limit
    label_mask = [1] * len(labels) + [0] * (sample_limit - len(labels))
    labels += [np.zeros(Config.relation_num)] * (sample_limit - len(labels))
    head_pos += [[0] * Config.mention_padding] * (sample_limit - len(head_pos))
    tail_pos += [[0] * Config.mention_padding] * (sample_limit - len(tail_pos))
    types += [('', '')] * (sample_limit - len(types))

    # document: sentence_num * token_num: 512
    # head/tail: sample_limit * mention_num: 3
    # labels: sample_limit * relation_num: 97
    # for training, return at most 90 pairs; for others, return at most 1800 pairs
    return Config.tokenizer.convert_tokens_to_ids(document), \
        labels, head_pos, tail_pos, label_mask, attn_mask, pair_ids, types
