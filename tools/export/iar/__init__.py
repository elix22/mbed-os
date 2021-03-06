import os
from os.path import sep, join, exists
from collections import namedtuple
from subprocess import Popen, PIPE
import shutil
import re
import sys

from tools.targets import TARGET_MAP
from tools.export.exporters import Exporter, TargetNotSupportedException
import json
from tools.export.cmsis import DeviceCMSIS
from multiprocessing import cpu_count

class IAR(Exporter):
    NAME = 'iar'
    TOOLCHAIN = 'IAR'

    #iar_definitions.json location
    def_loc = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', '..',
        'tools','export', 'iar', 'iar_definitions.json')

    #create a dictionary of the definitions
    with open(def_loc, 'r') as f:
        IAR_DEFS = json.load(f)

    #supported targets have a device name and corresponding definition in
    #iar_definitions.json
    TARGETS = [target for target, obj in TARGET_MAP.iteritems()
               if hasattr(obj, 'device_name') and
               obj.device_name in IAR_DEFS.keys() and "IAR" in obj.supported_toolchains]

    def iar_groups(self, grouped_src):
        """Return a namedtuple of group info
        Positional Arguments:
        grouped_src: dictionary mapping a group(str) to sources
            within it (list of file names)
        Relevant part of IAR template
        {% for group in groups %}
	    <group>
	        <name>group.name</name>
	        {% for file in group.files %}
	        <file>
	        <name>$PROJ_DIR${{file}}</name>
	        </file>
	        {% endfor %}
	    </group>
	    {% endfor %}
        """
        IARgroup = namedtuple('IARgroup', ['name','files'])
        groups = []
        for name, files in grouped_src.items():
            groups.append(IARgroup(name,files))
        return groups

    def iar_device(self):
        """Retrieve info from iar_definitions.json"""
        device_name =  TARGET_MAP[self.target].device_name
        device_info = self.IAR_DEFS[device_name]
        iar_defaults ={
            "OGChipSelectEditMenu": "",
            "CoreVariant": '',
            "GFPUCoreSlave": '',
            "GFPUCoreSlave2": 40,
            "GBECoreSlave": 35,
            "FPU2": 0,
            "NrRegs": 0,
        }

        iar_defaults.update(device_info)
        IARdevice = namedtuple('IARdevice', iar_defaults.keys())
        return IARdevice(**iar_defaults)

    def format_file(self, file):
        """Make IAR compatible path"""
        return join('$PROJ_DIR$',file)

    def format_src(self, srcs):
        """Group source files"""
        grouped = self.group_project_files(srcs)
        for group, files in grouped.items():
            grouped[group] = [self.format_file(src) for src in files]
        return grouped

    def generate(self):
        """Generate the .eww, .ewd, and .ewp files"""
        srcs = self.resources.headers + self.resources.s_sources + \
               self.resources.c_sources + self.resources.cpp_sources + \
               self.resources.objects + self.resources.libraries
        flags = self.flags
        c_flags = list(set(flags['common_flags']
                                    + flags['c_flags']
                                    + flags['cxx_flags']))
        # Flags set in template to be set by user in IDE
        template = ["--vla", "--no_static_destruction"]
        # Flag invalid if set in template
        # Optimizations are also set in template
        invalid_flag = lambda x: x in template or re.match("-O(\d|time|n)", x)
        flags['c_flags'] = [flag for flag in c_flags if not invalid_flag(flag)]

        try:
            debugger = DeviceCMSIS(self.target).debug.replace('-','').upper()
        except TargetNotSupportedException:
            debugger = "CMSISDAP"

        ctx = {
            'name': self.project_name,
            'groups': self.iar_groups(self.format_src(srcs)),
            'linker_script': self.format_file(self.resources.linker_script),
            'include_paths': [self.format_file(src) for src in self.resources.inc_dirs],
            'device': self.iar_device(),
            'ewp': sep+self.project_name + ".ewp",
            'debugger': debugger
        }
        ctx.update(flags)

        self.gen_file('iar/eww.tmpl', ctx, self.project_name + ".eww")
        self.gen_file('iar/ewd.tmpl', ctx, self.project_name + ".ewd")
        self.gen_file('iar/ewp.tmpl', ctx, self.project_name + ".ewp")

    @staticmethod
    def build(project_name, log_name="build_log.txt", cleanup=True):
        """ Build IAR project """
        # > IarBuild [project_path] -build [project_name]
        proj_file = project_name + ".ewp"
        cmd = ["IarBuild", proj_file, '-build', project_name]

        # IAR does not support a '0' option to automatically use all
        # available CPUs, so we use Python's multiprocessing library
        # to detect the number of CPUs available
        cpus_available = cpu_count()
        jobs = cpus_available if cpus_available else None

        # Only add the parallel flag if we're using more than one CPU
        if jobs:
            cmd += ['-parallel', str(jobs)]

        # Build the project
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        ret_code = p.returncode

        out_string = "=" * 10 + "STDOUT" + "=" * 10 + "\n"
        out_string += out
        out_string += "=" * 10 + "STDERR" + "=" * 10 + "\n"
        out_string += err

        if ret_code == 0:
            out_string += "SUCCESS"
        else:
            out_string += "FAILURE"

        print out_string

        if log_name:
            # Write the output to the log file
            with open(log_name, 'w+') as f:
                f.write(out_string)

        # Cleanup the exported and built files
        if cleanup:
            os.remove(project_name + ".ewp")
            os.remove(project_name + ".ewd")
            os.remove(project_name + ".eww")
            # legacy output file location
            if exists('.build'):
                shutil.rmtree('.build')
            if exists('BUILD'):
                shutil.rmtree('BUILD')

        if ret_code !=0:
            # Seems like something went wrong.
            return -1
        else:
            return 0
