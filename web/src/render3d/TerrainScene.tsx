import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';

import { screenToWorld, type Viewport } from '../render/viewport';
import type { CityArtifact } from '../types/city';

type Props = {
  artifact: CityArtifact | null;
  viewport: Viewport;
  visible: boolean;
};

const TERRAIN_EXAGGERATION = 80;

function classColor(cls: number, hillshade = 0.5): THREE.Color {
  let rgb: [number, number, number];
  switch (cls) {
    case 0:
      rgb = [28, 63, 78];
      break;
    case 1:
      rgb = [20, 31, 50];
      break;
    case 2:
      rgb = [34, 51, 77];
      break;
    case 3:
      rgb = [42, 62, 92];
      break;
    case 4:
      rgb = [29, 35, 46];
      break;
    case 5:
      rgb = [56, 69, 90];
      break;
    default:
      rgb = [22, 36, 56];
      break;
  }
  const lift = 0.75 + Math.max(0, Math.min(1, hillshade)) * 0.45;
  return new THREE.Color((rgb[0] / 255) * lift, (rgb[1] / 255) * lift, (rgb[2] / 255) * lift);
}

function buildTerrainMesh(artifact: CityArtifact): THREE.Mesh | null {
  const heights = artifact.terrain.heights;
  const rows = heights.length;
  const cols = heights[0]?.length ?? 0;
  if (!rows || !cols) return null;

  const extent = artifact.terrain.extent_m;
  const geometry = new THREE.PlaneGeometry(extent, extent, cols - 1, rows - 1);
  const pos = geometry.getAttribute('position') as THREE.BufferAttribute;
  const colors = new Float32Array(pos.count * 3);
  const terrainClasses = artifact.terrain.terrain_class_preview;
  const hillshade = artifact.terrain.hillshade_preview;

  let k = 0;
  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < cols; x += 1) {
      const wx = (x / Math.max(cols - 1, 1)) * extent;
      const wy = (y / Math.max(rows - 1, 1)) * extent;
      const z = (heights[y]?.[x] ?? 0) * TERRAIN_EXAGGERATION;
      pos.setXYZ(k, wx, wy, z);

      const cls = terrainClasses?.[y]?.[x] ?? 1;
      const shade = hillshade?.[y]?.[x] ?? 0.5;
      const c = classColor(cls, shade);
      colors[k * 3 + 0] = c.r;
      colors[k * 3 + 1] = c.g;
      colors[k * 3 + 2] = c.b;
      k += 1;
    }
  }

  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geometry.computeVertexNormals();

  const material = new THREE.MeshLambertMaterial({
    vertexColors: true,
    transparent: true,
    opacity: 0.96,
    side: THREE.DoubleSide,
  });

  const mesh = new THREE.Mesh(geometry, material);
  mesh.frustumCulled = false;
  return mesh;
}

export function TerrainScene({ artifact, viewport, visible }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.OrthographicCamera | null>(null);
  const terrainGroupRef = useRef<THREE.Group | null>(null);

  const terrainMesh = useMemo(() => (artifact ? buildTerrainMesh(artifact) : null), [artifact]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.pointerEvents = 'none';

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -4000, 8000);
    camera.up.set(0, 1, 0);

    const ambient = new THREE.AmbientLight(0x93b9d1, 0.55);
    scene.add(ambient);
    const key = new THREE.DirectionalLight(0xc5edff, 0.8);
    key.position.set(-0.4, 0.7, 1.4);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x5fbfe8, 0.28);
    fill.position.set(0.9, -0.2, 0.7);
    scene.add(fill);

    const terrainGroup = new THREE.Group();
    scene.add(terrainGroup);

    container.appendChild(renderer.domElement);

    rendererRef.current = renderer;
    sceneRef.current = scene;
    cameraRef.current = camera;
    terrainGroupRef.current = terrainGroup;

    return () => {
      renderer.dispose();
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
      scene.clear();
      rendererRef.current = null;
      sceneRef.current = null;
      cameraRef.current = null;
      terrainGroupRef.current = null;
    };
  }, []);

  useEffect(() => {
    const terrainGroup = terrainGroupRef.current;
    if (!terrainGroup) return;

    for (const child of [...terrainGroup.children]) {
      terrainGroup.remove(child);
      child.traverse((obj: THREE.Object3D) => {
        const mesh = obj as THREE.Mesh;
        const geom = mesh.geometry as THREE.BufferGeometry | undefined;
        const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
        geom?.dispose?.();
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose?.());
        else mat?.dispose?.();
      });
    }

    if (terrainMesh && visible) {
      terrainGroup.add(terrainMesh);
    }
  }, [terrainMesh, visible]);

  useEffect(() => {
    const container = containerRef.current;
    const renderer = rendererRef.current;
    const scene = sceneRef.current;
    const camera = cameraRef.current;
    if (!container || !renderer || !scene || !camera) return;

    const render = () => {
      const rect = container.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      renderer.setSize(rect.width, rect.height, false);

      if (artifact) {
        const extent = artifact.terrain.extent_m;
        const tl = screenToWorld(0, 0, extent, rect.width, rect.height, viewport);
        const br = screenToWorld(rect.width, rect.height, extent, rect.width, rect.height, viewport);
        const left = tl.x;
        const right = br.x;
        const top = tl.y;
        const bottom = br.y;
        camera.left = left;
        camera.right = right;
        camera.top = top;
        camera.bottom = bottom;

        const zBase = 1200;
        camera.position.set((left + right) * 0.5, (top + bottom) * 0.5, zBase);
        camera.lookAt((left + right) * 0.5, (top + bottom) * 0.5, 0);
      } else {
        camera.left = -1;
        camera.right = 1;
        camera.top = 1;
        camera.bottom = -1;
        camera.position.set(0, 0, 5);
        camera.lookAt(0, 0, 0);
      }
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
    };

    render();
    const ro = new ResizeObserver(render);
    ro.observe(container);
    return () => ro.disconnect();
  }, [artifact, viewport, visible]);

  return <div ref={containerRef} className="terrain-three-layer" aria-hidden="true" />;
}
