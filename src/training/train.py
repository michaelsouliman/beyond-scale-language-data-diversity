"""
Goal: making HF training script for model (e.g., llama v2) using raw text of informal and formal mathematics (unpaired data).

Inspiration:
- ref: SO accelerate + trainer: https://stackoverflow.com/questions/76675018/how-does-one-use-accelerate-with-the-hugging-face-hf-trainer
- ref: The unreasonable effectiveness of few-shot learning for machine translation https://arxiv.org/abs/2302.01398
- ref: colab: https://colab.research.google.com/drive/1io951Ex17-6OUaogCo7OiR-eXga_oUOH?usp=sharing
- ref: SO on collate: https://stackoverflow.com/questions/76879872/how-to-use-huggingface-hf-trainer-train-with-custom-collate-function/76929999#76929999

Looks very useful especially for peft:
- peft https://github.com/huggingface/trl/blob/main/examples/scripts/sft_trainer.py

python trl/examples/scripts/sft_trainer.py \
    --model_name meta-llama/Llama-2-7b-hf \
    --dataset_name timdettmers/openassistant-guanaco \
    --load_in_4bit \
    --use_peft \
    --batch_size 4 \
    --gradient_accumulation_steps 2

- qlora https://github.com/artidoro/qlora/blob/main/scripts/finetune_llama2_guanaco_7b.sh, 
- https://github.com/artidoro/qlora/blob/main/qlora.py

export CUDA_VISIBLE_DEVICES=6
"""
from pathlib import Path
from typing import Callable
import datasets
from datasets import load_dataset, interleave_datasets
import torch
from transformers import GPT2LMHeadModel, PreTrainedTokenizer, AutoTokenizer, Trainer, TrainingArguments, AutoConfig
import math

import sys
from training.reinit_and_smaller_llama2 import get_deafult_smallest_baby_llama2_v1_36m_0p036b, get_weight_norms, reinitialize_weights_gpt_neox_20B_inspired_4_llama2, get_full_llama7b_reinit
sys.path = [''] + sys.path
from training.utils import eval_hf, eval_hf_with_subsample, get_column_names, get_data_from_hf_dataset, group_texts, raw_dataset_2_lm_data

# -- Experiments 

def train():
    """
    I decided to make the string data close to context length of llama2 7B 4096 tokens.
    So if any string is shorter, the tokenize will padd it according to Claude.
    
    """
    # feel free to move the import statements if you want, sometimes I like everything in one place so I can easily copy-paste it into a script
    import datetime
    from pathlib import Path
    import datasets
    from datasets import load_dataset, interleave_datasets
    import torch
    import transformers
    from transformers import PreTrainedTokenizer
    from transformers import GPT2LMHeadModel, PreTrainedTokenizer, AutoTokenizer, Trainer, TrainingArguments, AutoConfig
    import random
    import math
    import os
    torch.cuda.empty_cache()
    # buffer_size = 500_000  # can't remember what this was for and doesn't seem to be anywhere
    probabilities = []
    data_mixture_name = None
    streaming = True
    data_files = [None]
    seed = 0
    split = 'train'
    max_length = 1024  # gpt2 context length
    shuffle = False
    report_to = 'none'  # safest default
    # CHUNK_SIZE = 16_896  # approximately trying to fill the llama2 context length of 4096
    batch_size = 2
    gradient_accumulation_steps = 2
    num_epochs = 1
    num_tokens_trained = None
    num_batches=1
    optim='paged_adamw_32bit'
    learning_rate=1e-5
    warmup_ratio=0.01
    weight_decay=0.01
    lr_scheduler_type='constant_with_warmup'
    # lr_scheduler_kwargs={}

    # -- Setup wandb
    import wandb
    # - Dryrun
    mode = 'dryrun'; seed = 0; report_to = 'none'

    # - Online (real experiment)
    mode = 'online'; seed = 0; report_to = 'wandb'

    # -- Train data sets
    # path, name, data_files, split = ['c4'], ['en'], [None], ['train']
    # - UDACA's
    path, name, data_files, split = ['UDACA/PileSubsets'], ['uspto'], [None], ['train']
    path, name, data_files, split = ['UDACA/PileSubsets'], ['pubmed'], [None], ['train']
    path, name, data_files, split = ['UDACA/PileSubsets', 'UDACA/PileSubsets'], ['uspto', 'pubmed'], [None, None], ['train', 'train']
    # - models
    # pretrained_model_name_or_path = 'gpt2'  # this is the smallest model gpt2, 124M params https://huggingface.co/gpt2 
    # pretrained_model_name_or_path = 'meta-llama/Llama-2-7b-hf'
    # pretrained_model_name_or_path = 'meta-llama/Llama-2-7b-chat-hf'
    # pretrained_model_name_or_path = 'meta-llama/Llama-2-13b-hf'
    # pretrained_model_name_or_path = 'meta-llama/Llama-2-70b-hf'
    # pretrained_model_name_or_path = 'mistralai/Mistral-7B-v0.1'
    # pretrained_model_name_or_path = 'baby_llama2_v1'
    pretrained_model_name_or_path = 'get_full_llama7b_reinit'
    # - important training details or it wont run, mem issues maybe
    max_steps = 2
    # max_steps = 300
    # max_steps = 866 # <- CHANGE THIS 12hs with with baby llama2 v1 36m 1, 32
    max_steps = 1_553  # 22-24hs llama2 full reinit 4*8=32=B 1024=L for 6.3M tokens
    # max_steps = 5_000
    # max_steps = 61_036  # 3.8 days for B=32 L=512 rate=5.43secs/it for 1B=1e9tokens
    # max_steps = 78_853 # 4.6 days L=512 B=32 r=5.43 ~1.21B 29,999MiB
    # max_steps = 30_517  # 11 days 1B L=512 B=32 r=31.31
    # max_steps = 1_761 # <- CHANGE THIS 12hs with with baby llama2 v1 36m 5, 6 0.2168M tokens
    # max_steps = 19_073 # <- CHANGE THIS  11 days with baby llama2 v1 36m 1, 32
    # max_steps = 306_000 # <- CHANGE THIS 12hs with with baby llama2 v1 36m 1, 32 35.1 tokens
    # max_length = 4096
    max_length = 1024
    # max_length = 512
    # max_length = 256
    num_batches=1
    # single gpu
    # batch_size, gradient_accumulation_steps = 1, 32  # e.g., choosing large number mabe for stability of training? 4 (per_device_train_batch_size) * 8 (gradient_accumulation_steps), based on alpaca https://github.com/tatsu-lab/stanford_alpaca 
    # batch_size, gradient_accumulation_steps = 6, 5  # e.g., choosing large number mabe for stability of training? 4 (per_device_train_batch_size) * 8 (gradient_accumulation_steps), based on alpaca https://github.com/tatsu-lab/stanford_alpaca 
    # batch_size, gradient_accumulation_steps = 5, 6  # e.g., choosing large number mabe for stability of training? 4 (per_device_train_batch_size) * 8 (gradient_accumulation_steps), based on alpaca https://github.com/tatsu-lab/stanford_alpaca 
    # batch_size, gradient_accumulation_steps = 4, 6  # e.g., choosing large number mabe for stability of training? 4 (per_device_train_batch_size) * 8 (gradient_accumulation_steps), based on alpaca https://github.com/tatsu-lab/stanford_alpaca 
    batch_size, gradient_accumulation_steps = 4, 8  # e.g., choosing large number mabe for stability of training? 4 (per_device_train_batch_size) * 8 (gradient_accumulation_steps), based on alpaca https://github.com/tatsu-lab/stanford_alpaca 
    # batch_size, gradient_accumulation_steps = 4, 16
    learning_rate=1e-4
    # learning_rate=1e-5
    optim='paged_adamw_32bit'
    # optim = 'adafactor'
    weight_decay=0.1
    warmup_ratio=0.01
    lr_scheduler_type='cosine'  # work as training argument
    # lr_scheduler_type='constant_with_warmup'  # work as training argument``
    # lr_scheduler_type='cosine_with_warmup'
    # lr_scheduler_kwargs={},  # ref: https://huggingface.co/docs/transformers/v4.37.0/en/main_classes/optimizer_schedules#transformers.SchedulerType 
    # -- multiple gpus 3 4096 context len
    # batch_size, gradient_accumulation_steps = 4, 8  # e.g., choosing large number mabe for stability of training? 4 (per_device_train_batch_size) * 8 (gradient_accumulation_steps), based on alpaca https://github.com/tatsu-lab/stanford_alpaca 
    # gradient_checkpointing = False
    gradient_checkpointing = True
    print(f'{batch_size=} {gradient_accumulation_steps=} {gradient_checkpointing=} {num_epochs=}')
    # -- Wandb
    CUDA_VISIBLE_DEVICES = os.environ.get('CUDA_VISIBLE_DEVICES')
    if CUDA_VISIBLE_DEVICES is not None:
        print(f"CUDA_VISIBLE_DEVICES = {CUDA_VISIBLE_DEVICES}")
    num_tokens_trained = max_steps * batch_size * max_length * num_batches 
    print(f'{num_tokens_trained=}')
    today = datetime.datetime.now().strftime('%Y-m%m-d%d-t%Hh_%Mm_%Ss')
    run_name = f'beyond scale: {path} ({today=} ({name=}) {data_mixture_name=} {probabilities=} {pretrained_model_name_or_path=} {data_files=} {max_steps=} {batch_size=} {num_tokens_trained=} {gradient_accumulation_steps=} {optim=} {learning_rate=} {max_length=} {weight_decay=} {warmup_ratio=} {CUDA_VISIBLE_DEVICES=})'
    print(f'\n---> {run_name=}\n')
    # - init wandb
    debug: bool = mode == 'dryrun'  # BOOL, debug?
    run = wandb.init(mode=mode, project="beyond-scale", name=run_name, save_code=True)
    wandb.config.update({"path": path, "name": name, "today": today, 'probabilities': probabilities, 'batch_size': batch_size, 'debug': debug, 'data_mixture_name': data_mixture_name, 'streaming': streaming, 'data_files': data_files, 'seed': seed, 'pretrained_model_name_or_path': pretrained_model_name_or_path, 'num_epochs': num_epochs, 'gradient_accumulation_steps': gradient_accumulation_steps, 'CUDA_VISIBLE_DEVICES': CUDA_VISIBLE_DEVICES})
    # run.notify_on_failure() # https://community.wandb.ai/t/how-do-i-set-the-wandb-alert-programatically-for-my-current-run/4891
    output_dir = Path(f'~/data/results_{today}/').expanduser() if not debug else Path(f'~/data/results/').expanduser()
    print(f'{output_dir=}')
    print(f'{debug=}')
    print(f'{wandb.config=}')

    # -- Load model and tokenizer  
    print(f'{pretrained_model_name_or_path=}')
    if pretrained_model_name_or_path == 'gpt2':
        from transformers import GPT2Tokenizer, GPT2LMHeadModel
        tokenizer = GPT2Tokenizer.from_pretrained(pretrained_model_name_or_path)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
            print(f'{tokenizer.pad_token=}')
        print(f'{tokenizer.eos_token=}')
        print(f'{tokenizer.eos_token_id=}')
        model = GPT2LMHeadModel.from_pretrained(pretrained_model_name_or_path)
        device = torch.device(f"cuda:{0}" if torch.cuda.is_available() else "cpu")
        model = model.to(device)
        block_size: int = tokenizer.model_max_length
        print(f'{block_size=}')
        print()
    elif 'Llama-2' in pretrained_model_name_or_path or 'Mistral' in pretrained_model_name_or_path:
        # - llama2
        from transformers import AutoModelForCausalLM, BitsAndBytesConfig, AutoTokenizer
        # bf16 or fp32
        torch_dtype = torch.bfloat16 if torch.cuda.get_device_capability(torch.cuda.current_device())[0] >= 8 else torch.float32 # if >= 8 ==> brain float 16 available or set to True if you always want fp32
        # get model
        model = AutoModelForCausalLM.from_pretrained(
            pretrained_model_name_or_path,
            # quantization_config=quantization_config,
            # device_map=device_map,  # device_map = None  https://github.com/huggingface/trl/blob/01c4a35928f41ba25b1d0032a085519b8065c843/examples/scripts/sft_trainer.py#L82
            trust_remote_code=True,
            torch_dtype=torch_dtype,
            use_auth_token=True,
        )
        # https://github.com/artidoro/qlora/blob/7f4e95a68dc076bea9b3a413d2b512eca6d004e5/qlora.py#L347C13-L347C13
        tokenizer = AutoTokenizer.from_pretrained(
            pretrained_model_name_or_path,
            # cache_dir=args.cache_dir,
            padding_side="right",
            use_fast=False, # Fast tokenizer giving issues.
            # tokenizer_type='llama' if 'llama' in args.model_name_or_path else None, # Needed for HF name change
            # tokenizer_type='llama',
            trust_remote_code=True,
            use_auth_token=True,
            # token=token,  # load from cat keys/brandos_hf_token.txt if you want to load it in python and not run huggingface-cli login
        )
        # - Ensure padding token is set TODO: how does this not screw up the fine-tuning? e.g., now model doesn't learn to predict eos since it's padded our by mask, ref: https://discuss.huggingface.co/t/why-does-the-falcon-qlora-tutorial-code-use-eos-token-as-pad-token/45954
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
            print(f'{tokenizer.pad_token=}')
        print(f'{tokenizer.eos_token=}')
        print(f'{ tokenizer.eos_token_id=}')
        # get context length for setting max length for training
        if hasattr(model.config, "context_length"):
            print("Context length:", model.config.context_length)
            max_length = model.config.context_length
        else:
            # CHUNK_SIZE = 16_896  # approximately trying to fill the llama2 context length of 4096
            max_length = 4096
        block_size: int = 4096
        print(f'{block_size=}')
    elif 'baby_llama2_v1' in pretrained_model_name_or_path or 'get_full_llama7b_reinit' in pretrained_model_name_or_path:
        model = get_deafult_smallest_baby_llama2_v1_36m_0p036b()
        model = get_full_llama7b_reinit(L=max_length)
        reinitialize_weights_gpt_neox_20B_inspired_4_llama2(model, L=max_length)
        # export HF_TOKEN=$(cat ~/keys/brandos_hf_token.txt)
        tokenizer = AutoTokenizer.from_pretrained('meta-llama/Llama-2-7b-hf', padding_side="right", use_fast=False, trust_remote_code=True, use_auth_token=True)
        device = torch.device(f"cuda:{0}" if torch.cuda.is_available() else "cpu")
        model = model.to(device)
        torch_dtype = torch.bfloat16 if torch.cuda.get_device_capability(torch.cuda.current_device())[0] >= 8 else torch.float32 # if >= 8 ==> brain float 16 available or set to True if you always want fp32
        model = model.to(torch_dtype)
        block_size: int = max_length
        print(f'{block_size=}')
    print("Number of parameters:", sum(p.numel() for p in model.parameters()))
    print(f"Total weight norm: {get_weight_norms(model)=}")
    print(f'{torch.cuda.device_count()=} (makes sure GPUs are visible and accesible to Pytorch.)')
    print(f'Model is currently on: {next(iter(model.parameters())).device=}')
    print(f'Model is currently on: {next(iter(model.parameters())).dtype=}')
    # Sanity check -- is loss random? lnV = -ln(1/V) = -ln(1/50257) = 10.82 since CE = avg_i v_i * ln(1/p_i) but only one token is right so vi = 1 for some i so CE = ln(1/p_i)
    print(f'vocab_size: {len(tokenizer)=} \nExpected random loss: {math.log(len(tokenizer))=}')
    print(f"CUDA version: {torch.version.cuda=}")
    eval_hf_with_subsample('UDACA/pile_openwebtext2', None, 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=2, print_str='> Eval OpenWebtext rand mdl')
    eval_hf_with_subsample('c4', 'en', 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=2, print_str='> Eval C4 rand mdl')
    eval_hf_with_subsample('wikitext', 'wikitext-103-v1', 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=2,  print_str='> Eval wikitext rand mdl')
    
    # --- Load datasets
    # -- Get train data set
    # - Load interleaved combined datasets
    train_datasets = [load_dataset(path, name, data_files=data_file, streaming=streaming, split=split).with_format("torch") for path, name, data_file, split in zip(path, name, data_files, split)]
    probabilities = [1.0/len(train_datasets) for _ in train_datasets]  
    print(f'{probabilities=}')
    # - Get raw train data set
    raw_train_datasets = interleave_datasets(train_datasets, probabilities)
    remove_columns = get_column_names(raw_train_datasets)  # remove all keys that are not tensors to avoid bugs in collate function in task2vec's pytorch data loader
    # - Get tokenized train data set
    # Note: Setting `batched=True` in the `dataset.map` function of Hugging Face's datasets library processes the data in batches rather than one item at a time, significantly speeding up the tokenization and preprocessing steps.
    tokenize_function = lambda examples: tokenizer(examples["text"])
    tokenized_train_datasets = raw_train_datasets.map(tokenize_function, batched=True, remove_columns=remove_columns)
    _group_texts = lambda examples : group_texts(examples, block_size)
    # - Get actual data set for lm training (in this case each seq is of length block_size, no need to worry about pad = eos since we are filling each sequence)
    lm_train_dataset = tokenized_train_datasets.map(_group_texts, batched=True)
    batch = get_data_from_hf_dataset(lm_train_dataset, streaming=streaming, batch_size=batch_size)
    print(f'{len(next(iter(batch))["input_ids"])=}')
    assert all(len(data_dict['input_ids']) == block_size for data_dict in iter(batch)), f'Error, some seq in batch are not of length {block_size}'
    train_dataset = lm_train_dataset

    # -- max steps manually decided depending on how many tokens we want to train on
    per_device_train_batch_size = batch_size
    print(f'{per_device_train_batch_size=}')
    print(f'{num_epochs=} {max_steps=}')

    # -- Training arguments and trainer instantiation ref: https://huggingface.co/docs/transformers/v4.31.0/en/main_classes/trainer#transformers.TrainingArguments
    print(f'{output_dir=}')
    training_args = TrainingArguments(
        output_dir=output_dir,  # The output directory where the model predictions and checkpoints will be written.
        # output_dir='.',  # The output directory where the model predictions and checkpoints will be written.
        max_steps=max_steps,  # TODO: hard to fix, see above
        per_device_train_batch_size=per_device_train_batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,  # based on alpaca https://github.com/tatsu-lab/stanford_alpaca, allows to process effective_batch_size = gradient_accumulation_steps * batch_size, num its to accumulate before opt update step
        gradient_checkpointing = gradient_checkpointing,  # TODO depending on hardware set to true?
        optim=optim,
        # warmup_steps=int(max_steps*warmup_ratio),  # TODO: once real training starts we can select this number for llama v2, what does llama v2 do to make it stable while v1 didn't?
        warmup_ratio=warmup_ratio,  # copying alpaca for now, number of steps for a linear warmup, TODO once real training starts change? 
        # weight_decay=0.01,  # TODO once real training change?
        weight_decay=weight_decay,  # TODO once real training change?
        learning_rate = learning_rate,  # TODO once real training change? anything larger than -3 I've had terrible experiences with
        max_grad_norm=1.0, # TODO once real training change?
        lr_scheduler_type=lr_scheduler_type,  # TODO once real training change? using what I've seen most in vision 
        # lr_scheduler_kwargs=lr_scheduler_kwargs,  # ref: https://huggingface.co/docs/transformers/v4.37.0/en/main_classes/optimizer_schedules#transformers.SchedulerType 
        logging_dir=Path('~/data/maf/logs').expanduser(),
        # save_steps=4000,  # alpaca does 2000, other defaults were 500
        save_steps=max_steps//3,  # alpaca does 2000, other defaults were 500
        # save_steps=1,  # alpaca does 2000, other defaults were 500
        # logging_steps=250,
        # logging_steps=50,  
        logging_first_step=True,
        # logging_steps=3,
        logging_steps=1,
        remove_unused_columns=False,  # TODO don't get why https://stackoverflow.com/questions/76879872/how-to-use-huggingface-hf-trainer-train-with-custom-collate-function/76929999#76929999 , https://claude.ai/chat/475a4638-cee3-4ce0-af64-c8b8d1dc0d90
        report_to=report_to,  # change to wandb!
        fp16=False,  # never ever set to True
        bf16=torch.cuda.get_device_capability(torch.cuda.current_device())[0] >= 8,  # if >= 8 ==> brain float 16 available or set to True if you always want fp32
    )
    print(f'{pretrained_model_name_or_path=}\n{optim=}\n{learning_rate=}')

    # -- Init Trainer
    print(f'{train_dataset=}')
    trainer = Trainer(
        model=model,
        args=training_args,  
        train_dataset=train_dataset,
    )

    # - Train
    trainer.train()
    trainer.save_model(output_dir=output_dir)  # TODO is this really needed? https://discuss.huggingface.co/t/do-we-need-to-explicity-save-the-model-if-the-save-steps-is-not-a-multiple-of-the-num-steps-with-hf/56745

    # --- Evaluation, NOTE: we are evaluating at the end not during training
    print()
    # -- Eval subsample
    print('---- Evaluate model on OpenWebtext')
    metrics = eval_hf_with_subsample('UDACA/pile_openwebtext2', None, 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=8)
    print(f'OpenWebtext (8 val samples): {metrics=}')
    print('---- Evaluate model on C4')
    metrics = eval_hf_with_subsample('c4', 'en', 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=8)
    print(f'C4 (8 val samples): {metrics=}')
    print('---- Evaluate model on wikitext-103-v1')
    metrics = eval_hf_with_subsample('wikitext', 'wikitext-103-v1', 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=8)
    print(f'Wikitext (8 val samples): {metrics=}')

    # -- Eval whole datasets
    print('---- Evaluate model on Whole OpenWebtext')
    metrics = eval_hf_with_subsample('UDACA/pile_openwebtext2', None, 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=None)
    print(f'OpenWebtext whole: {metrics=}')
    print('---- Evaluate model on Whole C4')
    metrics = eval_hf_with_subsample('c4', 'en', 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=None)
    print(f'C4 whole: {metrics=}')
    print('---- Evaluate model on Whole wikitext-103-v1')
    metrics = eval_hf_with_subsample('wikitext', 'wikitext-103-v1', 'validation', model, tokenizer, block_size, output_dir, max_eval_samples=None)
    print(f'Wikitext whole: {metrics=}')
    
    # -- Print config to show in log what this run was especially data set
    print(f'{wandb.config=}')
    print('Done!\a')

def main():  
    """Since accelerate config wants this, main_training_function: main"""
    train()

# -- Run __main__

if __name__ == '__main__':
    print(f'\n\n\n------------------- Running {__file__} -------------------')
    # -- Run tests and time it
    import time
    time_start = time.time()
    # -- Run tests
    main()
    # -- End tests, report how long it took in seconds, minutes, hours, days
    print(f'Time it took to run {__file__}: {time.time() - time_start} seconds, {(time.time() - time_start)/60} minutes, {(time.time() - time_start)/60/60} hours, {(time.time() - time_start)/60/60/24} days\a')
