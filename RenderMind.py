bl_info = {
    "name": "RenderMind",
    "author": "qwa3rr",
    "version": (6, 1, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > RenderMind",
    "description": "Smart Cycles optimizer with advanced scene analysis",
    "category": "Render",
}

import bpy
import json


def safe_getattr(obj, name, default=None):
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def format_int(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


class RenderMindManager:
    BACKUP_KEY = "rendermind_backup"

    @staticmethod
    def backup_scene_settings(scene):
        cycles = scene.cycles
        render = scene.render
        backup = {}

        cycles_keys = [
            "samples",
            "preview_samples",
            "max_bounces",
            "diffuse_bounces",
            "glossy_bounces",
            "transmission_bounces",
            "transparent_max_bounces",
            "volume_bounces",
            "use_denoising",
            "use_preview_denoising",
            "use_adaptive_sampling",
            "adaptive_threshold",
            "min_adaptive_samples",
            "device",
        ]

        render_keys = [
            "engine",
            "resolution_x",
            "resolution_y",
            "resolution_percentage",
        ]

        for key in cycles_keys:
            if hasattr(cycles, key):
                backup[key] = getattr(cycles, key)

        for key in render_keys:
            if hasattr(render, key):
                backup[key] = getattr(render, key)

        scene[RenderMindManager.BACKUP_KEY] = json.dumps(backup)

    @staticmethod
    def restore_scene_settings(scene):
        raw = scene.get(RenderMindManager.BACKUP_KEY)
        if not raw:
            return False

        try:
            data = json.loads(raw)
            cycles = scene.cycles
            render = scene.render

            for key, value in data.items():
                if hasattr(cycles, key):
                    setattr(cycles, key, value)
                elif hasattr(render, key):
                    setattr(render, key, value)

            return True
        except Exception:
            return False

    @staticmethod
    def material_analysis(material):
        info = {
            "transparent": False,
            "glass": False,
            "emission": False,
            "volume": False,
            "displacement": False,
            "complex_nodes": 0,
        }

        if not material or not material.use_nodes or not material.node_tree:
            return info

        nodes = material.node_tree.nodes
        info["complex_nodes"] = len(nodes)

        for node in nodes:
            ntype = node.type

            if ntype == 'BSDF_TRANSPARENT':
                info["transparent"] = True
            elif ntype in {'BSDF_GLASS', 'BSDF_REFRACTION'}:
                info["glass"] = True
                info["transparent"] = True
            elif ntype == 'EMISSION':
                info["emission"] = True
            elif ntype in {'PRINCIPLED_VOLUME', 'VOLUME_SCATTER', 'VOLUME_ABSORPTION'}:
                info["volume"] = True
            elif ntype == 'DISPLACEMENT':
                info["displacement"] = True

        return info

    @staticmethod
    def get_system_info(context):
        prefs = context.preferences
        scene = context.scene
        render = scene.render
        cycles = scene.cycles

        result = {
            "engine": render.engine,
            "cycles_device": safe_getattr(cycles, "device", "UNKNOWN"),
            "backend": "NONE",
            "gpu_active": False,
            "device_label": "CPU",
            "vulkan_supported": hasattr(prefs.system, "use_vulkan"),
            "vulkan_enabled": safe_getattr(prefs.system, "use_vulkan", None),
        }

        cycles_addon = prefs.addons.get("cycles")
        if not cycles_addon:
            return result

        cprefs = cycles_addon.preferences

        try:
            if hasattr(cprefs, "refresh_devices"):
                cprefs.refresh_devices()
        except Exception:
            pass

        result["backend"] = safe_getattr(cprefs, "compute_device_type", "NONE")

        devices = list(safe_getattr(cprefs, "devices", []))

        active_gpu_names = []
        active_cpu_names = []

        for d in devices:
            d_type = safe_getattr(d, "type", "UNKNOWN")
            d_name = safe_getattr(d, "name", "Unknown")
            d_use = bool(safe_getattr(d, "use", False))

            if not d_use:
                continue

            if d_type == "CPU":
                active_cpu_names.append(d_name)
            else:
                if d_name not in active_gpu_names:
                    active_gpu_names.append(d_name)

        if active_gpu_names:
            result["gpu_active"] = True
            result["device_label"] = active_gpu_names[0]
        elif active_cpu_names:
            result["device_label"] = active_cpu_names[0]

        return result

    @staticmethod
    def try_enable_best_device(context):
        prefs = context.preferences
        scene = context.scene
        cycles = scene.cycles
        render = scene.render

        render.engine = 'CYCLES'

        cycles_addon = prefs.addons.get("cycles")
        if not cycles_addon:
            return

        cprefs = cycles_addon.preferences

        try:
            if hasattr(cprefs, "refresh_devices"):
                cprefs.refresh_devices()
        except Exception:
            pass

        preferred_backends = ["OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"]

        for backend in preferred_backends:
            try:
                cprefs.compute_device_type = backend
                break
            except Exception:
                continue

        devices = list(safe_getattr(cprefs, "devices", []))
        active_gpu = False

        for d in devices:
            d_type = safe_getattr(d, "type", "UNKNOWN")
            try:
                if d_type == "CPU":
                    d.use = False
                else:
                    d.use = True
                    active_gpu = True
            except Exception:
                pass

        if active_gpu:
            try:
                cycles.device = 'GPU'
            except Exception:
                pass
        else:
            try:
                cycles.device = 'CPU'
            except Exception:
                pass

            for d in devices:
                if safe_getattr(d, "type", "UNKNOWN") == "CPU":
                    try:
                        d.use = True
                    except Exception:
                        pass

    @staticmethod
    def analyze_scene(scene):
        stats = {
            "objects_total": 0,
            "mesh_objects": 0,
            "lights": 0,
            "volumes": 0,
            "curves": 0,
            "hair_curves": 0,
            "instances": 0,
            "vertices": 0,
            "polygons": 0,
            "triangles_estimated": 0,
            "materials": 0,
            "transparent_materials": 0,
            "glass_materials": 0,
            "emission_materials": 0,
            "volume_materials": 0,
            "displacement_materials": 0,
            "avg_nodes_per_material": 0,
            "modifiers_total": 0,
            "subdivision_modifiers": 0,
            "array_modifiers": 0,
            "mirror_modifiers": 0,
            "geometry_nodes_modifiers": 0,
            "large_textures_guess": 0,
            "render_res_x": scene.render.resolution_x,
            "render_res_y": scene.render.resolution_y,
            "render_res_pct": scene.render.resolution_percentage,
            "complexity_score": 0,
            "noise_risk": 0,
            "memory_risk": 0,
        }

        unique_materials = {}
        total_node_count = 0

        for obj in scene.objects:
            stats["objects_total"] += 1

            if safe_getattr(obj, "instance_type", 'NONE') != 'NONE':
                stats["instances"] += 1

            if obj.type == 'MESH' and obj.data:
                stats["mesh_objects"] += 1
                mesh = obj.data

                stats["vertices"] += len(mesh.vertices)
                stats["polygons"] += len(mesh.polygons)
                stats["triangles_estimated"] += sum(max(0, len(p.vertices) - 2) for p in mesh.polygons)

                for mod in obj.modifiers:
                    stats["modifiers_total"] += 1
                    if mod.type == 'SUBSURF':
                        stats["subdivision_modifiers"] += 1
                    elif mod.type == 'ARRAY':
                        stats["array_modifiers"] += 1
                    elif mod.type == 'MIRROR':
                        stats["mirror_modifiers"] += 1
                    elif mod.type == 'NODES':
                        stats["geometry_nodes_modifiers"] += 1

                for slot in obj.material_slots:
                    mat = slot.material
                    if not mat:
                        continue
                    if mat.name not in unique_materials:
                        info = RenderMindManager.material_analysis(mat)
                        unique_materials[mat.name] = info
                        total_node_count += info["complex_nodes"]

            elif obj.type == 'LIGHT':
                stats["lights"] += 1
            elif obj.type == 'VOLUME':
                stats["volumes"] += 1
            elif obj.type == 'CURVE':
                stats["curves"] += 1
            elif obj.type == 'CURVES':
                stats["hair_curves"] += 1

        stats["materials"] = len(unique_materials)

        for info in unique_materials.values():
            if info["transparent"]:
                stats["transparent_materials"] += 1
            if info["glass"]:
                stats["glass_materials"] += 1
            if info["emission"]:
                stats["emission_materials"] += 1
            if info["volume"]:
                stats["volume_materials"] += 1
            if info["displacement"]:
                stats["displacement_materials"] += 1

        if stats["materials"] > 0:
            stats["avg_nodes_per_material"] = round(total_node_count / stats["materials"], 2)

        texture_count = 0
        for mat in bpy.data.materials:
            if not mat.use_nodes or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and getattr(node, "image", None):
                    image = node.image
                    size = safe_getattr(image, "size", [0, 0])
                    if len(size) >= 2:
                        w, h = size[0], size[1]
                        if w >= 2048 or h >= 2048:
                            texture_count += 1

        stats["large_textures_guess"] = texture_count

        render_pixels = (
            stats["render_res_x"] *
            stats["render_res_y"] *
            (stats["render_res_pct"] / 100.0) ** 2
        )

        score = 0
        score += stats["triangles_estimated"] * 0.0012
        score += stats["materials"] * 10
        score += stats["avg_nodes_per_material"] * 4
        score += stats["lights"] * 8
        score += stats["transparent_materials"] * 16
        score += stats["glass_materials"] * 22
        score += stats["volume_materials"] * 24
        score += stats["displacement_materials"] * 14
        score += stats["subdivision_modifiers"] * 12
        score += stats["geometry_nodes_modifiers"] * 12
        score += stats["hair_curves"] * 18
        score += stats["volumes"] * 18
        score += stats["large_textures_guess"] * 4
        score += render_pixels / 700000.0

        noise_risk = 0
        noise_risk += stats["lights"] * 3
        noise_risk += stats["emission_materials"] * 6
        noise_risk += stats["glass_materials"] * 8
        noise_risk += stats["transparent_materials"] * 6
        noise_risk += stats["volume_materials"] * 10

        memory_risk = 0
        memory_risk += stats["triangles_estimated"] * 0.0007
        memory_risk += stats["large_textures_guess"] * 8
        memory_risk += stats["subdivision_modifiers"] * 7
        memory_risk += stats["geometry_nodes_modifiers"] * 6
        memory_risk += stats["hair_curves"] * 8

        stats["complexity_score"] = int(score)
        stats["noise_risk"] = int(noise_risk)
        stats["memory_risk"] = int(memory_risk)

        return stats

    @staticmethod
    def calculate_optimal_settings(analysis):
        complexity = analysis["complexity_score"]
        noise_risk = analysis["noise_risk"]
        memory_risk = analysis["memory_risk"]

        samples = 96
        samples += int(complexity * 2.2)
        samples += int(noise_risk * 3.0)

        if analysis["glass_materials"] > 0:
            samples += 48
        if analysis["volume_materials"] > 0 or analysis["volumes"] > 0:
            samples += 64
        if analysis["emission_materials"] > 2:
            samples += 32

        if memory_risk > 180:
            samples = int(samples * 0.9)

        samples = clamp(samples, 64, 900)
        preview_samples = clamp(int(samples * 0.18), 16, 128)

        max_bounces = 8
        diffuse = 2
        glossy = 2
        transmission = 4
        transparent = 4
        volume = 0

        if analysis["glass_materials"] > 0 or analysis["transparent_materials"] > 0:
            transmission += 2
            transparent += 2

        if analysis["glass_materials"] > 3:
            transmission += 1
            transparent += 1

        if analysis["volume_materials"] > 0 or analysis["volumes"] > 0:
            volume = 2

        if analysis["complexity_score"] > 220:
            max_bounces = 10
            diffuse = 3
            glossy = 3

        if analysis["complexity_score"] > 420:
            max_bounces = 12
            diffuse = 4
            glossy = 4

        adaptive_threshold = 0.02
        min_adaptive = 24

        if samples >= 512:
            adaptive_threshold = 0.012
            min_adaptive = 32

        if samples >= 768:
            adaptive_threshold = 0.009
            min_adaptive = 48

        return {
            "samples": samples,
            "preview_samples": preview_samples,
            "max_bounces": clamp(max_bounces, 4, 12),
            "diffuse_bounces": clamp(diffuse, 1, 4),
            "glossy_bounces": clamp(glossy, 1, 4),
            "transmission_bounces": clamp(transmission, 2, 8),
            "transparent_max_bounces": clamp(transparent, 2, 8),
            "volume_bounces": clamp(volume, 0, 3),
            "use_denoising": True,
            "use_preview_denoising": True,
            "use_adaptive_sampling": True,
            "adaptive_threshold": adaptive_threshold,
            "min_adaptive_samples": min_adaptive,
        }

    @staticmethod
    def apply_settings(scene, settings):
        cycles = scene.cycles
        for key, value in settings.items():
            if hasattr(cycles, key):
                setattr(cycles, key, value)


class RENDERMIND_OT_optimize(bpy.types.Operator):
    bl_idname = "rendermind.optimize"
    bl_label = "Optimize"

    def execute(self, context):
        scene = context.scene

        try:
            RenderMindManager.backup_scene_settings(scene)
            RenderMindManager.try_enable_best_device(context)

            analysis = RenderMindManager.analyze_scene(scene)
            system_info = RenderMindManager.get_system_info(context)
            settings = RenderMindManager.calculate_optimal_settings(analysis)
            RenderMindManager.apply_settings(scene, settings)

            scene.rendermind_analysis_json = json.dumps(analysis)
            scene.rendermind_system_json = json.dumps(system_info)

            self.report({'INFO'}, "RenderMind optimization applied")
            return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"RenderMind failed: {e}")
            return {'CANCELLED'}


class RENDERMIND_OT_restore(bpy.types.Operator):
    bl_idname = "rendermind.restore"
    bl_label = "Restore Settings"

    def execute(self, context):
        scene = context.scene
        if RenderMindManager.restore_scene_settings(scene):
            self.report({'INFO'}, "Settings restored")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "No backup found")
            return {'CANCELLED'}


class RENDERMIND_PT_main_panel(bpy.types.Panel):
    bl_label = "RenderMind"
    bl_idname = "RENDERMIND_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'RenderMind'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        analysis = {}
        system_info = {}

        try:
            if scene.rendermind_analysis_json:
                analysis = json.loads(scene.rendermind_analysis_json)
        except Exception:
            analysis = {}

        try:
            if scene.rendermind_system_json:
                system_info = json.loads(scene.rendermind_system_json)
        except Exception:
            system_info = {}

        box = layout.box()
        col = box.column(align=True)
        col.operator("rendermind.optimize", text="Optimize", icon='CHECKMARK')
        col.operator("rendermind.restore", text="Restore", icon='LOOP_BACK')

        box = layout.box()
        box.label(text="System", icon='DESKTOP')
        col = box.column(align=True)

        if system_info:
            col.label(text=f"Engine: {system_info.get('engine', 'Unknown')}")
            col.label(text=f"Device: {'GPU' if system_info.get('gpu_active', False) else 'CPU'}")
            col.label(text=f"Backend: {system_info.get('backend', 'Unknown')}")
            col.label(text=f"Active: {system_info.get('device_label', 'Unknown')}")

            if system_info.get("vulkan_supported", False):
                vulkan_enabled = system_info.get("vulkan_enabled", None)
                if vulkan_enabled is True:
                    col.label(text="UI Vulkan: Enabled")
                elif vulkan_enabled is False:
                    col.label(text="UI Vulkan: Disabled")
                else:
                    col.label(text="UI Vulkan: Unknown")
            else:
                col.label(text="UI Vulkan: Not available")
        else:
            col.label(text="Press Optimize")

        box = layout.box()
        box.label(text="Scene Analysis", icon='SCENE_DATA')

        if analysis:
            grid = box.grid_flow(columns=2, align=True)
            grid.label(text=f"Objects: {analysis.get('objects_total', 0)}")
            grid.label(text=f"Meshes: {analysis.get('mesh_objects', 0)}")
            grid.label(text=f"Triangles: {format_int(analysis.get('triangles_estimated', 0))}")
            grid.label(text=f"Materials: {analysis.get('materials', 0)}")
            grid.label(text=f"Lights: {analysis.get('lights', 0)}")
            grid.label(text=f"Volumes: {analysis.get('volumes', 0)}")
            grid.label(text=f"Hair: {analysis.get('hair_curves', 0)}")
            grid.label(text=f"Instances: {analysis.get('instances', 0)}")
            grid.label(text=f"Glass: {analysis.get('glass_materials', 0)}")
            grid.label(text=f"Transparent: {analysis.get('transparent_materials', 0)}")
            grid.label(text=f"Emission: {analysis.get('emission_materials', 0)}")
            grid.label(text=f"Displacement: {analysis.get('displacement_materials', 0)}")
            grid.label(text=f"Subdivision: {analysis.get('subdivision_modifiers', 0)}")
            grid.label(text=f"Geo Nodes: {analysis.get('geometry_nodes_modifiers', 0)}")
            grid.label(text=f"Large Textures: {analysis.get('large_textures_guess', 0)}")
            grid.label(text=f"Complexity: {analysis.get('complexity_score', 0)}")

            box2 = layout.box()
            box2.label(text="Applied Settings", icon='PREFERENCES')

            settings_grid = box2.grid_flow(columns=2, align=True)
            settings_grid.label(text=f"Samples: {scene.cycles.samples}")
            settings_grid.label(text=f"Preview: {scene.cycles.preview_samples}")
            settings_grid.label(text=f"Max Bounces: {scene.cycles.max_bounces}")
            settings_grid.label(text=f"Diffuse: {scene.cycles.diffuse_bounces}")
            settings_grid.label(text=f"Glossy: {scene.cycles.glossy_bounces}")
            settings_grid.label(text=f"Transmission: {scene.cycles.transmission_bounces}")
            settings_grid.label(text=f"Transparent: {scene.cycles.transparent_max_bounces}")
            settings_grid.label(text=f"Volume: {scene.cycles.volume_bounces}")
            settings_grid.label(text=f"Adaptive: {'On' if scene.cycles.use_adaptive_sampling else 'Off'}")
            settings_grid.label(text=f"Denoise: {'On' if scene.cycles.use_denoising else 'Off'}")
        else:
            box.label(text="Press Optimize")


classes = (
    RENDERMIND_OT_optimize,
    RENDERMIND_OT_restore,
    RENDERMIND_PT_main_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.rendermind_analysis_json = bpy.props.StringProperty(default="")
    bpy.types.Scene.rendermind_system_json = bpy.props.StringProperty(default="")


def unregister():
    del bpy.types.Scene.rendermind_system_json
    del bpy.types.Scene.rendermind_analysis_json

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()