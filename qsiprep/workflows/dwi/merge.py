# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Merge and denoise dwi images
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: init_dwi_preproc_wf
.. autofunction:: init_dwi_derivatives_wf

"""

import os
from collections import defaultdict

import nibabel as nb
from nipype import logging

from nipype.interfaces.fsl import Split as FSLSplit
from nipype.interfaces.mrtrix3 import DWIDenoise
from nipype.pipeline import engine as pe
from nipype.interfaces import utility as niu

from ...interfaces import DerivativesDataSink, MergeDWIs

from ...interfaces.reports import FunctionalSummary
from fmriprep.engine import Workflow

# dwi workflows
from .util import init_dwi_reference_wf

DEFAULT_MEMORY_MIN_GB = 0.01
LOGGER = logging.getLogger('nipype.workflow')


def init_merge_and_denoise_wf(dwi_files,
                              dwi_denoise_window,
                              denoise_before_combining,
                              combine_all_dwis,
                              mem_gb=1,
                              omp_nthreads=1):
    """

    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from qsiprep.workflows.dwi import init_merge_and_denoise_wf
        wf = init_merge_and_dwnoise_wf(
                                       ['/completely/made/up/path/sub-01_dwi.nii.gz'],
                                       dwi_denoise_window=7,
                                       denoise_before_combining=True,
                                       combine_all_dwis=True)

    **Parameters**

        dwi_files : list
            list of dwi NIfTI files
        dwi_denoise_window : int
            window size in voxels for ``dwidenoise``. Must be odd. If 0,
            ``dwidwenoise`` will not be run
        denoise_before_combining : bool
            run ``dwidenoise`` before combining dwis. Requires ``combine_all_dwis``
            If ``dwi_denoise_window > 0`` and this is ``False``, then ``dwidenoise``
            is run on the merged dwi series.
        combine_all_dwis : bool
            Combine all dwi sequences within a session into a single data set


    **Inputs**

        dwi_files
            list of dwi files


    **Outputs**

        merged_image
            dwi series, resampled to T1w space
        merged_bval
            bvals from merged images
        merged_bvec
            bvecs from merged images
        noise_image
            image(s) created by ``dwidenoise``
    """

    # Check if the output from one PE can be used as the RPE for the other
    # Build workflow
    workflow = Workflow(name="merge_and_denoise_wf")

    inputnode = pe.MapNode(
        niu.IdentityInterface(fields=['dwi_files']), name='inputnode')
    inputnode.inputs.dwi_files = dwi_files

    outputnode = pe.Node(
        niu.IdentityInterface(fields=[
            'merged_image', 'merged_bval', 'merged_bvec', 'noise_image']),
        name='outputnode')

    merge_dwis = pe.Node(MergeDWIs(), name='merge_dwis')
    workflow.connect([(inputnode, merge_dwis, [('dwi_files', 'original_files')])])

    if dwi_denoise_window > 0:
        denoise = pe.MapNode(DWIDenoise(), iterfield='in_file',
                             name='denoise',
                             extent=(dwi_denoise_window, dwi_denoise_window,
                                     dwi_denoise_window))
        if denoise_before_combining:
            workflow.connect([
                (inputnode, denoise, [('dwi_files', 'in_file')]),
                (denoise, merge_dwis, [('out_file', 'dwi_files')]),
                (denoise, outputnode, [('noise', 'noise_image')]),
                (merge_dwis, outputnode, [('out_dwi', 'merged_image'),
                                          ('out_bval', 'merged_bval'),
                                          ('out_bvec', 'merged_bvec')])
            ])
        else:
            workflow.connect([
                (inputnode, merge_dwis, [('dwi_files', 'dwi_files')]),
                (merge_dwis, denoise, [('out_dwi', 'in_file')]),
                (denoise, outputnode, [('noise', 'noise_image'),
                                       ('out_file', 'merged_image')]),
                (merge_dwis, outputnode, [('out_bval', 'merged_bval'),
                                          ('out_bvec', 'merged_bvec')])
            ])
    else:
        workflow.connect([
            (inputnode, merge_dwis, [('dwi_files', 'dwi_files')]),
            (merge_dwis, outputnode, [('out_dwi', 'merged_image'),
                                      ('out_bval', 'merged_bval'),
                                      ('out_bvec', 'merged_bvec')])
        ])

    return workflow
