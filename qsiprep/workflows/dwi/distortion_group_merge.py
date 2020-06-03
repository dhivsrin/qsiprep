"""
Merging Distortion Groups
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: init_dwi_hmc_wf
.. autofunction:: init_dwi_model_hmc_wf

"""
import logging
from nipype.interfaces import utility as niu
import nipype.pipeline.engine as pe
from .derivatives import init_dwi_derivatives_wf
from ...engine import Workflow
from ...interfaces import DerivativesDataSink
from ...interfaces.mrtrix import MRTrixGradientTable
from ...interfaces.reports import GradientPlot, SeriesQC
from ...interfaces.dwi_merge import AveragePEPairs, MergeDWIs
from .qc import init_mask_overlap_wf


DEFAULT_MEMORY_MIN_GB = 0.01
LOGGER = logging.getLogger('nipype.workflow')


def init_distortion_group_merge_wf(merging_strategy, inputs_list, hmc_model, reportlets_dir,
                                   harmonize_b0_intensities, b0_threshold, output_prefix,
                                   source_file, output_dir, template, shoreline_iters,
                                   mem_gb=3, omp_nthreads=1,
                                   name="distortion_group_merge_wf"):
    """Create an unbiased intramodal template for a subject. This aligns the b=0 references
    from all the scans of a subject. Can be rigid, affine or nonlinear (BSplineSyN).

    **Parameters**
        inputs_list: list of inputs
            List if identifiers for inputs. There will be bvals, bvecs, niis and original
            bvecs.
        merging_strategy: str
            'average': averages images that originally sampled the same q-space coordinate
            'concat': concatenates images in the 4th dimension


    **Inputs**

        [workflow_name]_image...
            One input for each volume in each input image.
        [workflow_name]_bval...
            One input for each input image. path to the corresponding bval file
        [workflow_name]_bvec...
            One input for each input image. path to the corresponding final bvec file
        [workflow_name]_original_bvec...
            One input for each input image. Path to the original bvec file
        [workflow_name]_original_image...
            One input for each input image. Path to the original dwi file
        [workflow_name]_raw_concatenated_image
            One input for each input image. Path to the original images after concatenation
        [workflow_name]_confounds
            One input for each input image. Path to the confounds files
        [workflow_name]_b0_ref
            One input for each input image. Path to the b=0 reference image

    **Outputs**
        merged_image
            The input images merged into a single image (averaged or concatenated)
        merged_bval
            The bvals corresponding to merged_image
        merged_bvec
            The bvecs corresponding to merged_image
        merged_qc
            The Before/After QC file

    """
    workflow = Workflow(name=name)
    sanitized_inputs = [name.replace('-', '_') for name in inputs_list]
    input_names = []
    for suffix in ["_image", "_bval", "_bvec", "_original_bvec", "_b0_ref",
                   "_original_image", "_raw_concatenated_image"]:
        input_names += [name + suffix for name in sanitized_inputs]
    inputnode = pe.Node(niu.IdentityInterface(fields=input_names), name='inputnode')
    outputnode = pe.Node(
        niu.IdentityInterface(fields=["merged_image", "merged_bval", "merged_bvec", "merged_qc"]),
        name='outputnode')

    num_inputs = len(input_names)
    merge_images = pe.Node(niu.Merge(num_inputs), name='merge_images')
    merge_bval = pe.Node(niu.Merge(num_inputs), name='merge_bval')
    merge_bvec = pe.Node(niu.Merge(num_inputs), name='merge_bvec')
    merge_original_bvec = pe.Node(niu.Merge(num_inputs), name='merge_original_bvec')
    merge_original_image = pe.Node(niu.Merge(num_inputs), name='merge_original_image')
    merge_b0_refs = pe.Node(niu.Merge(num_inputs), name='merge_b0_refs')
    merge_raw_concatenated_image = pe.Node(niu.Merge(num_inputs),
                                           name='merge_raw_concatenated_image')

    # Merge the input data from each distortion group: safe even if eddy was used
    for input_num, input_name in enumerate(sanitized_inputs):
        merge_input_name = 'in%d' % (input_num + 1)
        workflow.connect([
            (inputnode, merge_images, [(input_name + "_image", merge_input_name)]),
            (inputnode, merge_bval, [(input_name + "_bval", merge_input_name)]),
            (inputnode, merge_bvec, [(input_name + "_bvec", merge_input_name)]),
            (inputnode, merge_original_bvec, [(input_name + "_original_bvec", merge_input_name)]),
            (inputnode, merge_original_image, [(input_name + "_original_image",
                                                merge_input_name)]),
            (inputnode, merge_raw_concatenated_image, [(input_name + "_raw_concatenated_image",
                                                        merge_input_name)]),
            (inputnode, merge_b0_refs, [(input_name + "_b0_ref", merge_input_name)])
        ])

    if merging_strategy.lower() == 'average':
        distortion_merger = pe.Node(AveragePEPairs(), name='distortion_merger')
        workflow.connect([
            (merge_original_bvec, distortion_merger, [('out', 'original_bvec_files')])
        ])
    elif merging_strategy.startswith('concat'):
        distortion_merger = pe.Node(MergeDWIs(), name='distortion_merger')

    workflow.connect([
        (merge_images, distortion_merger, [('out', 'dwi_files')]),
        (merge_bval, distortion_merger, [('out', 'bval_files')]),
        (merge_bvec, distortion_merger, [('out', 'bvec_files')]),
        (merge_original_image, distortion_merger, [('out', 'bids_dwi_files')]),
        (merge_raw_concatenated_image, distortion_merger, [('out', 'raw_concatenated_files')]),
        (merge_b0_refs, distortion_merger, [('out', 'b0_refs')])
    ])

    # CONNECT TO DERIVATIVES
    gtab_t1 = pe.Node(MRTrixGradientTable(), name='gtab_t1')
    t1_dice_calc = init_mask_overlap_wf(name='t1_dice_calc')
    gradient_plot = pe.Node(GradientPlot(), name='gradient_plot', run_without_submitting=True)
    ds_report_gradients = pe.Node(
        DerivativesDataSink(suffix='sampling_scheme', source_file=source_file),
        name='ds_report_gradients', run_without_submitting=True,
        mem_gb=DEFAULT_MEMORY_MIN_GB)

    dwi_derivatives_wf = init_dwi_derivatives_wf(
        output_prefix=output_prefix,
        source_file=source_file,
        output_dir=output_dir,
        output_spaces=["T1w"],
        template=template,
        write_local_bvecs=False,
        hmc_model=hmc_model,
        shoreline_iters=shoreline_iters)

    # Combine all the QC measures for a series QC
    series_qc = pe.Node(SeriesQC(output_file_name=output_prefix), name='series_qc')
    ds_series_qc = pe.Node(
        DerivativesDataSink(desc='ImageQC', suffix='dwi', source_file=source_file,
                            base_directory=output_dir),
        name='ds_series_qc', run_without_submitting=True,
        mem_gb=DEFAULT_MEMORY_MIN_GB)

    workflow.connect([
        (inputnode, series_qc, [
            ('raw_qc_file', 'pre_qc'),
            ('confounds', 'confounds_file')]),
        (t1_dice_calc, series_qc, [('outputnode.dice_score', 't1_dice_score')]),
        (series_qc, ds_series_qc, [('series_qc_file', 'in_file')]),
        (inputnode, dwi_derivatives_wf, [('dwi_files', 'inputnode.source_file')]),
        (inputnode, outputnode, [('hmc_optimization_data', 'hmc_optimization_data')]),
        (distortion_merger, series_qc, [('merged_raw_concatenated', 't1_qc')]),
        (transform_dwis_t1, t1_dice_calc, [
            ('outputnode.resampled_dwi_mask', 'inputnode.dwi_mask')]),
        (outputnode, gradient_plot, [('bvecs_t1', 'final_bvec_file')]),
        (distortion_merger, gtab_t1, [('out_bval', 'bval_file'),
                                      ('out_bvec', 'bvec_file')]),
        (inputnode, t1_dice_calc, [
            ('t1_mask', 'inputnode.anatomical_mask')]),
        (gtab_t1, outputnode, [('gradient_file', 'gradient_table_t1')]),
        (outputnode, dwi_derivatives_wf,
         [('dwi_t1', 'inputnode.dwi_t1'),
          ('dwi_mask_t1', 'inputnode.dwi_mask_t1'),
          ('cnr_map_t1', 'inputnode.cnr_map_t1'),
          ('bvals_t1', 'inputnode.bvals_t1'),
          ('bvecs_t1', 'inputnode.bvecs_t1'),
          ('local_bvecs_t1', 'inputnode.local_bvecs_t1'),
          ('t1_b0_ref', 'inputnode.t1_b0_ref'),
          ('gradient_table_t1', 'inputnode.gradient_table_t1'),
          ('confounds', 'inputnode.confounds'),
          ('hmc_optimization_data', 'inputnode.hmc_optimization_data')]),
        (distortion_merger, gradient_plot, [
            ('out_bvec', 'orig_bvec_files'),
            ('out_bval', 'orig_bval_files'),
            ('original_images', 'source_files')]),
        (gradient_plot, ds_report_gradients, [('plot_file', 'in_file')])
    ])

    # Fill-in datasinks of reportlets seen so far
    for node in workflow.list_node_names():
        if node.split('.')[-1].startswith('ds_report'):
            workflow.get_node(node).inputs.base_directory = reportlets_dir

    return workflow