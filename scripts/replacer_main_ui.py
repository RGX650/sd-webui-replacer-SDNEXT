from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
import uuid
import modules.scripts as scripts
import modules.shared
from modules import script_callbacks
import os
import random
import time
import numpy as np
import gradio as gr
from modules.shared import opts, state
from PIL import Image, PngImagePlugin
import torch
from modules import scripts, shared, ui_common, postprocessing, call_queue
from scripts.replacer_generate import generate, applyHiresFix, getLastUsedSeed
from modules.call_queue import wrap_gradio_gpu_call, wrap_queued_call, wrap_gradio_call
from modules.ui_common import create_output_panel
from modules.ui_symbols import refresh as refresh_symbol
from scripts.replacer_options import EXT_NAME, EXT_NAME_LOWER, getSaveDir
from scripts.replacer_options import getDetectionPromptExamples, getPositivePromptExamples
from scripts.replacer_options import getNegativePromptExamples, useFirstPositivePromptFromExamples
from scripts.replacer_options import useFirstNegativePromptFromExamples, getHiresFixPositivePromptSuffixExamples
from modules.shared import cmd_opts
from modules import sd_samplers
from modules.ui_components import ToolButton
from modules import ui



class Script(scripts.Script):
    def __init__(self) -> None:
        super().__init__()

    def title(self):
        return EXT_NAME

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        return ()







def on_ui_tabs():
    with gr.Blocks() as replacer:
        
        tab_index = gr.State(value=0)

        with gr.Row():

            with gr.Column():

                with gr.Row():
                    placeholder = getDetectionPromptExamples()[0]
                    detectionPrompt = gr.Textbox(label="Detection prompt", 
                                        show_label=True, 
                                        lines=1, 
                                        elem_classes=["detectionPrompt"],
                                        placeholder=placeholder)
                    
                    gr.Examples(
                        examples=getDetectionPromptExamples(),
                        inputs=detectionPrompt,
                        label="",
                    )

                with gr.Row():
                    placeholder = None
                    if (useFirstPositivePromptFromExamples()):
                        placeholder = getPositivePromptExamples()[0]
                    
                    positvePrompt = gr.Textbox(label="Positve prompt", 
                                        show_label=True, 
                                        lines=1, 
                                        elem_classes=["positvePrompt"],
                                        placeholder=placeholder)

                    gr.Examples(
                        examples=getPositivePromptExamples(),
                        inputs=positvePrompt,
                        label="",
                    )

                with gr.Row():
                    placeholder = None
                    if (useFirstNegativePromptFromExamples()):
                        placeholder = getNegativePromptExamples()[0]

                    negativePrompt = gr.Textbox(label="Negative prompt", 
                                        show_label=True, 
                                        lines=1, 
                                        elem_classes=["negativePrompt"],
                                        placeholder=placeholder)

                    
                    gr.Examples(
                        examples=getNegativePromptExamples(),
                        inputs=negativePrompt,
                        label="",  
                    )

                run_button = gr.Button("Run")


                with gr.Accordion("Advanced options", open=False):
                    with gr.Row():
                        sampler = gr.Dropdown(
                            label='Sampling method',
                            elem_id="replacer_sampler",
                            choices=sd_samplers.visible_sampler_names(),
                            value="DPM++ 2M SDE Karras"
                        )

                        steps = gr.Slider(
                            label='Steps',
                            value=20,
                            step=1,
                            minimum=1,
                            maximum=150,
                            elem_id="replacer_steps"
                        )

                    with gr.Row():
                        box_threshold = gr.Slider(label='Box Threshold',
                            value=0.3, elem_id="replacer_box_threshold",
                            minimum=0.0, maximum=1.0, step=0.05)
                        mask_expand = gr.Slider(label='Mask Expand',
                            value=35, elem_id="replacer_mask_expand",
                            minimum=0, maximum=100, step=1)
                        mask_blur = gr.Slider(label='Mask Blur',
                            value=4, elem_id="replacer_mask_blur",
                            minimum=0, maximum=10, step=1)

                    with gr.Row():
                        from scripts.sam import sam_model_list, refresh_sam_models
                        from scripts.dino import dino_model_list

                        sam_model_name = gr.Dropdown(label="SAM Model", choices=sam_model_list,
                            value=sam_model_list[0] if len(sam_model_list) > 0 else None)
                        sam_refresh_models = ToolButton(value=refresh_symbol)
                        sam_refresh_models.click(refresh_sam_models, sam_model_name,sam_model_name)

                        dino_model_name = gr.Dropdown(label="GroundingDINO Model", choices=dino_model_list, value=dino_model_list[0])

                    with gr.Row():
                        cfg_scale = gr.Slider(label='CFG Scale',
                            value=5.5, elem_id="replacer_cfg_scale",
                            minimum=1.0, maximum=30.0, step=0.5)
                        denoise = gr.Slider(label='Denoising',
                            value=1.0, elem_id="replacer_denoise",
                            minimum=0.0, maximum=1.0, step=0.01)
                        inpaint_padding = gr.Slider(label='Padding',
                            value=20, elem_id="replacer_inpaint_padding",
                            minimum=0, maximum=100, step=1)
                        
                    with gr.Row():
                        inpainting_fill = gr.Radio(label='Masked content',
                            choices=['fill', 'original', 'latent noise', 'latent nothing'],
                            value='fill', type="index", elem_id="replacer_inpainting_fill")

                    with gr.Row():
                        width = gr.Slider(label='width',
                            value=512, elem_id="replacer_width",
                            minimum=64, maximum=2048, step=8)
                        batch_count = gr.Slider(label='batch count',
                            value=1, elem_id="replacer_batch_count",
                            minimum=1, maximum=10, step=1)

                    with gr.Row():
                        height = gr.Slider(label='height',
                            value=512, elem_id="replacer_height",
                            minimum=64, maximum=2048, step=8)
                        batch_size = gr.Slider(label='batch size',
                            value=1, elem_id="replacer_batch_size",
                            minimum=1, maximum=10, step=1)


                    with gr.Row():
                        upscalerForImg2Img = gr.Dropdown(
                            value=None,
                            choices=[x.name for x in shared.sd_upscalers],
                            label="Upscaler for img2Img",
                        )

                        #if cmd_opts.use_textbox_seed:
                            #seed = gr.Textbox(label='Seed', value="", elem_id="replacer_seed", min_width=100)
                        #else:
                            #seed = gr.Number(label='Seed', value=-1, elem_id="replacer_seed", min_width=100, precision=0)
                            
                        seed = gr.Number(label='Seed', value=-1, elem_id="replacer_seed", min_width=100, precision=0)
                        
                        random_seed = ToolButton(
                            ui.random_symbol,
                            elem_id="replacer_random_seed",
                            label='Random seed'
                        )
                        reuse_seed = ToolButton(
                            ui.reuse_symbol,
                            elem_id="replacer_reuse_seed",
                            label='Reuse seed'
                        )



                with gr.Tabs(elem_id="mode_extras"):
                    with gr.TabItem('Single Image', id="single_image", elem_id="single_tab") as tab_single:
                        image = gr.Image(label="Source", source="upload", interactive=True, type="pil", elem_id="image", image_mode="RGBA")

                    with gr.TabItem('Batch Process', id="batch_process", elem_id="batch_process_tab") as tab_batch:
                        image_batch = gr.Files(label="Batch Process", interactive=True, elem_id="extras_image_batch")

                    with gr.TabItem('Batch from Directory', id="batch_from_directory", elem_id="batch_directory_tab") as tab_batch_dir:
                        input_batch_dir = gr.Textbox(label="Input directory", **shared.hide_dirs, placeholder="A directory on the same machine where the server is running.", elem_id="input_batch_dir")
                        output_batch_dir = gr.Textbox(label="Output directory", **shared.hide_dirs, placeholder="Leave blank to save images to the default path.", elem_id="output_batch_dir")
                        show_batch_dir_results = gr.Checkbox(label='Show result images', value=True, elem_id="show_batch_dir_results")





            with gr.Column():
                with gr.Row():
                    img2img_gallery, generation_info, html_info, html_log = \
                        create_output_panel(EXT_NAME_LOWER, getSaveDir())
                    
                with gr.Row():
                    apply_hires_fix_button = gr.Button("Apply HiresFix")

                with gr.Row():
                    with gr.Accordion("HiresFix options", open=False):
                        with gr.Row():
                            hf_upscaler = gr.Dropdown(
                                value="ESRGAN_4x",
                                choices=[x.name for x in shared.sd_upscalers],
                                label="Upscaler",
                            )

                            hf_steps = gr.Slider(
                                label='Hires steps',
                                value=4,
                                step=1,
                                minimum=1,
                                maximum=150,
                                elem_id="hf_steps"
                            )

                            hf_cfg_scale = gr.Slider(
                                label='Hires CFG Scale',
                                value=1.0,
                                step=0.5,
                                minimum=1.0,
                                maximum=30.0,
                                elem_id="hf_cfg_scale"
                            )

                        with gr.Row():
                            hf_sampler = gr.Dropdown(
                                label='Hires sampling method',
                                elem_id="hf_sampler",
                                choices=["Use same sampler"] + sd_samplers.visible_sampler_names(),
                                value="Use same sampler"
                            )

                            hf_denoise = gr.Slider(
                                label='Hires Denoising',
                                value=0.35,
                                step=0.01,
                                minimum=0.0,
                                maximum=1.0,
                                elem_id="hf_denoise"
                            )
                        
                        with gr.Row():
                            placeholder = None
                            placeholder = getHiresFixPositivePromptSuffixExamples()[0]

                            hfPositivePromptSuffix = gr.Textbox(
                                label="Suffix for positive prompt", 
                                show_label=True, 
                                lines=1, 
                                elem_classes=["hfPositivePromptSuffix"],
                                placeholder=placeholder
                            )

                            gr.Examples(
                                examples=getHiresFixPositivePromptSuffixExamples(),
                                inputs=hfPositivePromptSuffix,
                                label="",
                            )

                        with gr.Row():
                            hf_size_limit = gr.Slider(
                                label='Limit render size',
                                value=2000,
                                step=1,
                                minimum=1000,
                                maximum=10000,
                                elem_id="hf_size_limit"
                            )


        def tab_single_on_select():
            return 0, gr.Button.update(visible=True)

        def tab_batch_on_select():
            return 1, gr.Button.update(visible=False)
        
        def tab_batch_dir_on_select():
            return 2, gr.Button.update(visible=False)

        tab_single.select(fn=tab_single_on_select, inputs=[], outputs=[tab_index, apply_hires_fix_button])
        tab_batch.select(fn=tab_batch_on_select, inputs=[], outputs=[tab_index, apply_hires_fix_button])
        tab_batch_dir.select(fn=tab_batch_dir_on_select, inputs=[], outputs=[tab_index, apply_hires_fix_button])

        run_button.click(
            fn=wrap_gradio_gpu_call(generate, extra_outputs=[None, '', '']),
            inputs=[
                detectionPrompt,
                positvePrompt,
                negativePrompt,
                tab_index,
                image,
                image_batch,
                input_batch_dir,
                output_batch_dir,
                show_batch_dir_results,
                upscalerForImg2Img,
                seed,
                sampler,
                steps,
                box_threshold,
                mask_expand,
                mask_blur,
                sam_model_name,
                dino_model_name,
                cfg_scale,
                denoise,
                inpaint_padding,
                inpainting_fill,
                width,
                batch_count,
                height,
                batch_size,
            ],
            outputs=[
                img2img_gallery,
                generation_info,
                html_info,
                html_log,
            ]
        )


        apply_hires_fix_button.click(
            fn=wrap_gradio_gpu_call(applyHiresFix, extra_outputs=[None, '', '']),
            inputs=[
                hf_upscaler,
                hf_steps,
                hf_sampler,
                hf_denoise,
                hf_cfg_scale,
                hfPositivePromptSuffix,
                hf_size_limit,
            ],
            outputs=[
                img2img_gallery,
                generation_info,
                html_info,
                html_log,
            ]
        )


        random_seed.click(
            fn=lambda: -1,
            inputs=[
            ],
            outputs=[
                seed,
            ]
        )

        reuse_seed.click(
            fn=getLastUsedSeed,
            inputs=[
            ],
            outputs=[
                seed,
            ]
        )




    return [(replacer, EXT_NAME, EXT_NAME)]



script_callbacks.on_ui_tabs(on_ui_tabs)


