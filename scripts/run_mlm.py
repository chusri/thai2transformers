# coding=utf-8
# Copyright 2020 The HuggingFace Team All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Fine-tuning the library models for masked language modeling (BERT, ALBERT, RoBERTa...) on a text file or a dataset.
Here is the full list of checkpoints on the hub that can be fine-tuned by this script:
https://huggingface.co/models?filter=masked-lm
"""
# You can also adapt this script on your own masked language modeling task. Pointers for this are left as comments.

import torch
import logging
import os
import sys
import glob
from dataclasses import dataclass, field
from typing import Optional

import transformers
from transformers import (
    RobertaConfig,
    RobertaForMaskedLM,
    CamembertTokenizer,
    MODEL_FOR_MASKED_LM_MAPPING,
    DataCollatorForLanguageModeling,
    HfArgumentParser,
    Trainer,
    TrainingArguments,
    set_seed,
)
from data_loader import MemmapLineByLineTextDataset, MemmapConcatTextDataset


logger = logging.getLogger(__name__)
MODEL_CONFIG_CLASSES = list(MODEL_FOR_MASKED_LM_MAPPING.keys())
MODEL_TYPES = tuple(conf.model_type for conf in MODEL_CONFIG_CLASSES)


def is_main_process(rank):
    return rank in [-1, 0]


@dataclass
class ModelArguments:
    """
    Arguments pertaining to which model/config/tokenizer we are going to fine-tune, or train from scratch.
    """

    model_name_or_path: Optional[str] = field(
        default=None,
        metadata={
            "help": "The model checkpoint for weights initialization."
            "Don't set if you want to train a model from scratch."
        },
    )
    model_type: Optional[str] = field(
        default=None,
        metadata={"help": "If training from scratch, pass a model type from the list: " + ", ".join(MODEL_TYPES)},
    )
    config_name: Optional[str] = field(
        default=None, metadata={"help": "Pretrained config name or path if not the same as model_name"}
    )
    tokenizer_name_or_path: Optional[str] = field(
        default=None, metadata={"help": "Pretrained tokenizer name or path if not the same as model_name"}
    )
    cache_dir: Optional[str] = field(
        default=None, metadata={"help": "Where do you want to store the pretrained models downloaded from s3"}
    )
    use_fast_tokenizer: bool = field(
        default=True,
        metadata={"help": "Whether to use one of the fast tokenizer (backed by the tokenizers library) or not."},
    )


@dataclass
class DataTrainingArguments:
    """
    Arguments pertaining to what data we are going to input our model for training and eval.
    """
    train_dir: str = field(
        metadata={"help": "The input training data dir (dir that contain text files)."}
    )  # Non-standard
    eval_dir: str = field(
        metadata={"help": "The input evaluation data dir (dir that contain text files)."},
    )  # Non-standard
    overwrite_cache: bool = field(
        default=False, metadata={"help": "Overwrite the cached training and evaluation sets"}
    )
    max_seq_length: Optional[int] = field(
        default=None,
        metadata={
            "help": "The maximum total input sequence length after tokenization. Sequences longer "
            "than this will be truncated."
        },
    )
    preprocessing_num_workers: Optional[int] = field(
        default=None,
        metadata={"help": "The number of processes to use for the preprocessing."},
    )
    mlm_probability: float = field(
        default=0.15, metadata={"help": "Ratio of tokens to mask for masked language modeling loss"}
    )
    line_by_line: bool = field(
        default=False,
        metadata={"help": "Whether distinct lines of text in the dataset are to be handled as distinct sequences."},
    )
    pad_to_max_length: bool = field(
        default=False,
        metadata={
            "help": "Whether to pad all samples to `max_seq_length`. "
            "If False, will pad the samples dynamically when batching to the maximum length in the batch."
        },
    )
    datasets_cache_dir: str = field(
        default=None, metadata={'help': 'The directory for datasets cache.'}
    )  # Non-standard
    mlm: bool = field(
        default=False,
        metadata={"help": "Train with masked-language modeling loss instead of language modeling."}
    )
    datasets_type: str = field(
        default='MemmapLineByLineTextDataset', metadata={'help': 'Type of datasets object.'}
    )

    def __post_init__(self):
        if self.train_dir is None and self.eval_dir is None:
            raise ValueError("Need either a dataset name or a training/validation file.")


@dataclass
class ArchitectureArguments:
    num_hidden_layers: int = field(
        default=12,
        metadata={'help': 'number of hidden layers (L)'}
        )
    hidden_size: int = field(
        default=768,
        metadata={'help': 'number of hidden size (H)'}
        )
    intermediate_size: int = field(
        default=3072,
        metadata={'help': 'number of intermediate_size'}
        )
    num_attention_head: int = field(
        default=12,
        metadata={'help': 'number of attention head (A)'}
        )


@dataclass
class CustomOthersArguments:
    add_space_token: bool = field(
        default=False, metadata={'help': 'Add spacial token for tokenizer.'}
        )
    ext: str = field(
        default='.txt', metadata={'help': 'Extension of training and evaluation files.'}
        )
    model_dir: Optional[str] = field(
        default=None, metadata={'help': 'Dir of the checkpoint.'}
        )
    tokenize_chunksize: Optional[int] = field(
        default=2500, metadata={'help': 'Chunksize for tokenize function.'}
        )


def main():
    # See all possible arguments in src/transformers/training_args.py
    # or by passing the --help flag to this script.
    # We now keep distinct sets of args, for a cleaner separation of concerns.

    parser = HfArgumentParser((ModelArguments, DataTrainingArguments, TrainingArguments,
                               ArchitectureArguments, CustomOthersArguments))

    (model_args, data_args, training_args,
        arch_args, custom_args) = parser.parse_args_into_dataclasses()

    # Setup logging
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        level=logging.INFO if is_main_process(training_args.local_rank) else logging.WARN,
    )

    # Log on each process the small summary:
    logger.warning(
        f"Process rank: {training_args.local_rank}, device: {training_args.device}, n_gpu: {training_args.n_gpu}"
        + f"distributed training: {bool(training_args.local_rank != -1)}, 16-bits training: {training_args.fp16}"
    )
    # Set the verbosity to info of the Transformers logger (on main process only):
    if is_main_process(training_args.local_rank):
        transformers.utils.logging.set_verbosity_info()
    logger.info("Training/evaluation parameters %s", training_args)

    # Set seed before initializing model.
    set_seed(training_args.seed)

    # Get the datasets: you can either provide your own CSV/JSON/TXT training and evaluation files (see below)
    # or just provide the name of one of the public datasets available on the hub at https://huggingface.co/datasets/
    # (the dataset will be downloaded automatically from the datasets Hub
    #
    # For CSV/JSON files, this script will use the column called 'text' or the first column. You can easily tweak this
    # behavior (see below)
    #
    # In distributed training, the load_dataset function guarantee that only one local process can concurrently
    # download the dataset.
    train_files = list(sorted(glob.glob(f'{data_args.train_dir}/*.{custom_args.ext}')))[:1]#[:2]
    validation_files = list(sorted(glob.glob(f'{data_args.eval_dir}/*.{custom_args.ext}')))[:1]#[:2]

    # See more about loading any type of standard or custom dataset (from files, python dict, pandas DataFrame, etc) at
    # https://huggingface.co/docs/datasets/loading_datasets.html.

    # Load pretrained model and tokenizer
    #
    # Distributed training:
    # The .from_pretrained methods guarantee that only one local process can concurrently
    # download model & vocab.
    # Create config for LM model

    tokenizer = CamembertTokenizer.from_pretrained(
        model_args.tokenizer_name_or_path, use_fast=model_args.use_fast_tokenizer)
    if custom_args.add_space_token:
        logging.info('Special token `<th_roberta_space_token>` will be added'
                     'to the CamembertTokenizer instance.')
        tokenizer.additional_special_tokens = ['<s>NOTUSED', '</s>NOTUSED',
                                               '<th_roberta_space_token>']

    if custom_args.ext == 'txt':
        if len(train_files) > 1 or len(validation_files) > 1:
            raise NotImplementedError('only one txt file support for now')
        if data_args.datasets_type == 'MemmapLineByLineTextDataset':
            datasets = {
                'train': MemmapLineByLineTextDataset(
                    tokenizer, train_files[0], data_args.max_seq_length,
                    os.path.join(data_args.datasets_cache_dir, 'train'),
                    custom_args.tokenize_chunksize, data_args.overwrite_cache
                ),
                'validation': MemmapLineByLineTextDataset(
                    tokenizer, validation_files[0], data_args.max_seq_length,
                    os.path.join(data_args.datasets_cache_dir, 'validation'),
                    custom_args.tokenize_chunksize, data_args.overwrite_cache
                )
            }
        elif data_args.datasets_type == 'MemmapConcatTextDataset':
            datasets = {
                'train': MemmapConcatTextDataset(
                    tokenizer, train_files[0], data_args.max_seq_length,
                    os.path.join(data_args.datasets_cache_dir, 'train'),
                    custom_args.tokenize_chunksize, data_args.overwrite_cache
                ),
                'validation': MemmapConcatTextDataset(
                    tokenizer, validation_files[0], data_args.max_seq_length,
                    os.path.join(data_args.datasets_cache_dir, 'validation'),
                    custom_args.tokenize_chunksize, data_args.overwrite_cache
                )
            }
        else:
            raise NotImplementedError(f'No specified datasets type {data_args.datasets_type}')
    else:
        raise NotImplementedError(f'not supprt {custom_args.ext},'
                                  f'but this should be possible to support.')

    config = RobertaConfig(
        vocab_size=tokenizer.vocab_size,
        type_vocab_size=1,
        # roberta base as default
        num_hidden_layers=arch_args.num_hidden_layers,  # L
        hidden_size=arch_args.hidden_size,  # H
        intermediate_size=arch_args.intermediate_size,
        num_attention_head=arch_args.num_attention_head,  # A
        # roberta large
        # num_hidden_layers=24,
        # hidden_size=1024,
        # intermediate_size=4096,
        # num_attention_head=16
    )

    # Initialize model
    model = RobertaForMaskedLM(config=config)

    if custom_args.model_dir is not None:
        model_path = os.path.join(custom_args.model_dir, 'pytorch_model.bin')
        print(f'[INFO] Load pretrianed model (state_dict) from {model_path}')
        # Use strict=False to kept model compatible with older version,
        # so we can bumb transformers version up and use new datasets library
        # see this issues https://github.com/huggingface/transformers/issues/6882
        # The program itself will run but does it has any side effect?
        # Maybe bad idea?
        model.load_state_dict(state_dict=torch.load(model_path), strict=False)
        # If we did not add strict=False, this will raise Error since the keys are not match
        # RuntimeError: Error(s) in loading state_dict for RobertaForMaskedLM:
        #     Missing key(s) in state_dict: "roberta.embeddings.position_ids".
        #     Unexpected key(s) in state_dict: "roberta.pooler.dense.weight",
        # "roberta.pooler.dense.bias".

    data_collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer, mlm=data_args.mlm, mlm_probability=data_args.mlm_probability)

    # Initialize trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=datasets["train"],
        eval_dataset=datasets["validation"],
        data_collator=data_collator,
        prediction_loss_only=True
    )

    # Training
    if custom_args.model_dir is not None:
        trainer.train(model_path=custom_args.model_dir)
    else:
        trainer.train()

    # save
    output_model_dir = os.path.join(training_args.output_dir, 'roberta_thai')
    logging.info(" Save final model to '%s'.", output_model_dir)
    trainer.save_model(output_model_dir)

    if trainer.is_world_process_zero():
        output_tokenizer_dir = os.path.join(training_args.output_dir, 'roberta_thai_tokenizer')
        tokenizer.save_pretrained(output_tokenizer_dir)

    return
    # evaluate
    trainer.evaluate()


def _mp_fn(index):
    # For xla_spawn (TPUs)
    main()


if __name__ == "__main__":
    main()
