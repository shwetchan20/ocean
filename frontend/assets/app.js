import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Scene setup
// ---------------------------------------------------------------------------
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x06101c);

const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 2000);
camera.position.set(26, 20, 34);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
$('scene').appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.target.set(0, -3, 0);

scene.add(new THREE.AmbientLight(0x9edcff, 1.4));
const sun = new THREE.DirectionalLight(0xffffff, 1.8);
sun.position.set(12, 24, 12);
scene.add(sun);

const fieldGroup = new THREE.Group();   // temperature/salinity/chlorophyll point layers
const currentGroup = new THREE.Group(); // current-vector arrows
const isoGroup = new THREE.Group();     // isosurface / isotherm contour lines
const obsGroup = new THREE.Group();     // instrument markers
const trackGroup = new THREE.Group();   // glider dashed tracks
scene.add(fieldGroup, currentGroup, isoGroup, obsGroup, trackGroup);

function resize() {
  const el = $('scene');
  renderer.setSize(el.clientWidth, el.clientHeight, false);
  camera.aspect = el.clientWidth / el.clientHeight;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
resize();

// ---------------------------------------------------------------------------
// Color palettes
// ---------------------------------------------------------------------------
const PALETTES = {
  thermal: ['#173c9e', '#1dc7d9', '#d7e85c', '#ff7a3b'],
  viridis: ['#440154', '#31688e', '#35b779', '#fde725'],
  coolwarm: ['#3b4cc0', '#eeeeee', '#b40426'],
};
const FIXED = {
  salinity: ['#0a1f6b', '#1dc7d9'],
  chlorophyll: ['#08321a', '#3fae4e', '#d7f542'],
};

function paletteColors(stops) {
  return stops.map((h) => new THREE.Color(h));
}
function colorAt(colors, t) {
  t = Math.max(0, Math.min(1, t));
  const x = t * (colors.length - 1);
  const i = Math.min(colors.length - 2, Math.floor(x));
  const f = x - i;
  return colors[i].clone().lerp(colors[i + 1], f);
}
function normValue(v, min, max, log) {
  if (log) {
    const lo = Math.max(min, 1e-6);
    const hi = Math.max(max, lo * 1.001);
    const vv = Math.max(v, lo);
    return (Math.log(vv) - Math.log(lo)) / (Math.log(hi) - Math.log(lo));
  }
  return (v - min) / ((max - min) || 1);
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let META = null;
let OBS = [];
let selectedObs = null;
let playing = false;
let playTimer = null;

// spatial mapping: lon/lat -> scene x/z, depth -> scene y
let mapScale = 1, centerLat = 0, centerLon = 0, depthNorm = 300;

function fitMapping() {
  const lonSpan = META.lon_range[1] - META.lon_range[0];
  const latSpan = META.lat_range[1] - META.lat_range[0];
  centerLon = (META.lon_range[0] + META.lon_range[1]) / 2;
  centerLat = (META.lat_range[0] + META.lat_range[1]) / 2;
  mapScale = 26 / Math.max(lonSpan, latSpan, 1e-6);
  const maxDepth = Math.max(...META.depths, 1);
  depthNorm = maxDepth / 6; // deepest layer sits ~6 scene units down before exaggeration
}
const toX = (lon) => (lon - centerLon) * mapScale;
const toZ = (lat) => (lat - centerLat) * mapScale;
const toY = (depth) => -(depth / depthNorm) * verticalExaggeration();
const fromXZ = (x, z) => ({ lon: x / mapScale + centerLon, lat: z / mapScale + centerLat });

const verticalExaggeration = () => parseFloat($('vertical').value);
const opacityVal = () => parseFloat($('opacity').value);
const timeIndex = () => parseInt($('time').value, 10);
const depthIndex = () => parseInt($('depth').value, 10);

// ---------------------------------------------------------------------------
// Layer / field cache (per time+variable) so scrubbing the depth slider
// doesn't refetch the whole 3D field every frame
// ---------------------------------------------------------------------------
const fieldCache = new Map();
async function fetchField(variable, time) {
  const key = `${variable}:${time}`;
  if (fieldCache.has(key)) return fieldCache.get(key);
  const data = await fetch(`/api/ocean?time=${time}&variable=${variable}`).then((r) => r.json());
  fieldCache.set(key, data);
  return data;
}
function clearFieldCache() { fieldCache.clear(); }

function idx3(i, j, k, ny, nz) { return (i * ny + j) * nz + k; }

function activeLayers() {
  return Array.from(document.querySelectorAll('.layerToggle'))
    .filter((el) => el.checked)
    .map((el) => el.dataset.var);
}
function primaryVariable() {
  const order = ['temperature', 'salinity', 'chlorophyll'];
  const active = activeLayers();
  return order.find((v) => active.includes(v)) || 'temperature';
}

// ---------------------------------------------------------------------------
// Render: field layers (points, one depth slice per active variable)
// ---------------------------------------------------------------------------
async function renderFieldLayers() {
  fieldGroup.clear();
  if (!$('modelToggle').checked) return;

  const vars = activeLayers();
  const ti = timeIndex();
  const di = depthIndex();
  const op = opacityVal();

  let primaryData = null;

  for (let li = 0; li < vars.length; li++) {
    const variable = vars[li];
    let data;
    try {
      data = await fetchField(variable, ti);
    } catch (e) { continue; }
    if (variable === primaryVariable()) primaryData = data;

    const ny = data.lon.length, nz = data.depth.length;
    const vals = data.values;
    const geo = new THREE.BufferGeometry();
    const pos = [], cols = [];

    const isPrimary = variable === primaryVariable();
    const colors = isPrimary
      ? paletteColors(PALETTES[$('palette').value])
      : paletteColors(FIXED[variable] || PALETTES.thermal);
    const log = isPrimary && $('logScale').checked;

    for (let i = 0; i < data.lat.length; i++) {
      for (let j = 0; j < data.lon.length; j++) {
        const v = vals[idx3(i, j, di, ny, nz)];
        const y = toY(data.depth[di]) + li * 0.04; // tiny offset to avoid z-fighting between layers
        pos.push(toX(data.lon[j]), y, toZ(data.lat[i]));
        const c = colorAt(colors, normValue(v, data.min, data.max, log));
        cols.push(c.r, c.g, c.b);
      }
    }
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
    const pts = new THREE.Points(geo, new THREE.PointsMaterial({ size: 0.19, vertexColors: true, transparent: true, opacity: op }));
    fieldGroup.add(pts);
  }

  // reference wireframe box at the slice depth
  if (vars.length) {
    const d = META.depths[di];
    const lonSpan = (META.lon_range[1] - META.lon_range[0]) * mapScale;
    const latSpan = (META.lat_range[1] - META.lat_range[0]) * mapScale;
    const box = new THREE.Mesh(
      new THREE.BoxGeometry(lonSpan + 2, 0.05, latSpan + 2),
      new THREE.MeshBasicMaterial({ color: 0x0e4c68, wireframe: true, transparent: true, opacity: 0.3 })
    );
    box.position.y = toY(d);
    fieldGroup.add(box);
    $('depthVal').textContent = `${d} m`;
    $('depthLabel').textContent = `${d} m`;
  }

  updateColorbar(primaryData);
}

function updateColorbar(data) {
  if (!data) {
    $('minVal').textContent = '—'; $('maxVal').textContent = '—';
    $('cbMin').textContent = '—'; $('cbMax').textContent = '—';
    return;
  }
  $('minVal').textContent = data.min.toFixed(2);
  $('maxVal').textContent = data.max.toFixed(2);
  $('cbMin').textContent = data.min.toFixed(1);
  $('cbMax').textContent = data.max.toFixed(1);
  const meta = META.variables.find((v) => v.id === data.variable);
  $('cbTitle').textContent = meta ? `${meta.label} (${meta.unit})` : data.variable;
  const stops = PALETTES[$('palette').value];
  $('cbGradient').style.background = `linear-gradient(90deg, ${stops.join(',')})`;
  $('timeLabel').textContent = data.time;
}

// ---------------------------------------------------------------------------
// Render: current vectors (arrow field at the selected depth slice)
// ---------------------------------------------------------------------------
async function renderCurrents() {
  currentGroup.clear();
  if (!$('currentsToggle').checked || !$('modelToggle').checked) return;
  const ti = timeIndex(), di = depthIndex();
  let data;
  try {
    data = await fetch(`/api/currents?time=${ti}&depth_index=${di}&stride=2`).then((r) => r.json());
  } catch (e) { return; }
  if (!data.vectors) return;
  const y = toY(data.depth) + 0.1;
  const maxMag = Math.max(...data.vectors.map((v) => Math.hypot(v.u, v.v)), 1e-6);
  data.vectors.forEach((v) => {
    const mag = Math.hypot(v.u, v.v);
    if (mag < 1e-4) return;
    const dir = new THREE.Vector3(v.u, 0, v.v).normalize();
    const origin = new THREE.Vector3(toX(v.lon), y, toZ(v.lat));
    const length = 0.4 + 1.3 * (mag / maxMag);
    const color = new THREE.Color().setHSL(0.16, 0.9, 0.55 + 0.15 * (mag / maxMag));
    const arrow = new THREE.ArrowHelper(dir, origin, length, color.getHex(), length * 0.35, length * 0.18);
    currentGroup.add(arrow);
  });
}

// ---------------------------------------------------------------------------
// Render: isosurface / isotherm contours (stacked marching-squares polylines)
// ---------------------------------------------------------------------------
let isoDomain = { min: 0, max: 1 };

async function refreshIsoDomain() {
  const variable = primaryVariable();
  const ti = timeIndex();
  const data = await fetchField(variable, ti);
  isoDomain = { min: data.min, max: data.max };
}

async function renderIsosurface() {
  isoGroup.clear();
  $('isoControls').hidden = !$('isoToggle').checked;
  if (!$('isoToggle').checked) return;

  await refreshIsoDomain();
  const variable = primaryVariable();
  const ti = timeIndex();
  const t = parseFloat($('isoThreshold').value);
  const threshold = isoDomain.min + t * (isoDomain.max - isoDomain.min);
  $('isoThresholdVal').textContent = threshold.toFixed(2);

  let data;
  try {
    data = await fetch(`/api/isosurface?time=${ti}&variable=${variable}&threshold=${threshold}`).then((r) => r.json());
  } catch (e) { return; }

  const color = 0xc98bff;
  data.layers.forEach((layer) => {
    const y = toY(layer.depth) + 0.06;
    layer.polylines.forEach((line) => {
      const pts = line.map(([lon, lat]) => new THREE.Vector3(toX(lon), y, toZ(lat)));
      if (pts.length < 2) return;
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.85 });
      isoGroup.add(new THREE.Line(geo, mat));
    });
  });
}

// Set the initial threshold slider position once we know the server's default.
async function primeIsoThreshold() {
  const variable = primaryVariable();
  const ti = timeIndex();
  const data = await fetch(`/api/isosurface?time=${ti}&variable=${variable}`).then((r) => r.json());
  await refreshIsoDomain();
  const span = (isoDomain.max - isoDomain.min) || 1;
  const t = (data.threshold - isoDomain.min) / span;
  $('isoThreshold').value = Math.max(0, Math.min(1, t)).toFixed(2);
}

// ---------------------------------------------------------------------------
// Render: instrument markers + selected glider track
// ---------------------------------------------------------------------------
const OBS_COLOR = { Argo: 0x55d8ff, Glider: 0xffbf69, CTD: 0xc6f36b, BGC: 0xe18cff };

function renderObsMarkers(filter = 'all') {
  obsGroup.clear();
  const list = $('obsList');
  list.innerHTML = '';
  if (!$('insituToggle').checked) return;

  OBS.filter((o) => filter === 'all' || o.type === filter).forEach((o) => {
    const row = document.createElement('div');
    row.className = 'obs' + (selectedObs && selectedObs.id === o.id ? ' selected' : '');
    row.innerHTML = `<b>${o.id}</b><small>${o.type} · ${o.lat.toFixed(1)}°N · ${o.lon.toFixed(1)}°E</small>`;
    row.onclick = () => selectObs(o);
    list.appendChild(row);

    const color = OBS_COLOR[o.type] || 0xffffff;
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(0.32, 16, 10),
      new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.3 })
    );
    mesh.position.set(toX(o.lon), 0.15, toZ(o.lat));
    mesh.userData = o;
    obsGroup.add(mesh);
  });
}

async function selectObs(o) {
  selectedObs = o;
  $('obsTitle').textContent = o.id;
  $('obsMeta').textContent = `${o.type} · ${o.lat.toFixed(2)}°N, ${o.lon.toFixed(2)}°E · ${o.timestamp}`;
  renderObsMarkers(document.querySelector('#filters button.active')?.dataset.type || 'all');

  try {
    const profile = await fetch(`/api/observations/${o.id}/profile?time=${timeIndex()}`).then((r) => r.json());
    drawChart($('chartTemp'), profile.depths, profile.observed.temperature, profile.model.temperature, '#55d8ff', '#ff7a3b', '°C');
    drawChart($('chartSal'), profile.depths, profile.observed.salinity, profile.model.salinity, '#8be36b', '#c98bff', 'PSU');
  } catch (e) { /* profile unavailable */ }

  trackGroup.clear();
  if (o.type === 'Glider') {
    try {
      const t = await fetch(`/api/observations/${o.id}/track`).then((r) => r.json());
      if (t.track && t.track.length > 1) {
        const pts = t.track.map((p) => new THREE.Vector3(toX(p.lon), 0.12, toZ(p.lat)));
        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        const mat = new THREE.LineDashedMaterial({ color: 0xffbf69, dashSize: 0.25, gapSize: 0.15 });
        const line = new THREE.Line(geo, mat);
        line.computeLineDistances();
        trackGroup.add(line);
      }
      // vertical dive-depth marker line
      const maxDepth = Math.max(...(t.track.length ? [700] : [0]), 700);
      const top = new THREE.Vector3(toX(o.lon), 0.12, toZ(o.lat));
      const bottom = new THREE.Vector3(toX(o.lon), toY(maxDepth), toZ(o.lat));
      const dgeo = new THREE.BufferGeometry().setFromPoints([top, bottom]);
      const dmat = new THREE.LineDashedMaterial({ color: 0xffbf69, dashSize: 0.2, gapSize: 0.12 });
      const dline = new THREE.Line(dgeo, dmat);
      dline.computeLineDistances();
      trackGroup.add(dline);
    } catch (e) { /* no track */ }
  }
}

// ---------------------------------------------------------------------------
// Depth-vs-variable chart (canvas 2D, no dependencies)
// ---------------------------------------------------------------------------
function drawChart(canvas, depths, seriesA, seriesB, colorA, colorB, unit) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const all = [...seriesA, ...seriesB].filter((v) => Number.isFinite(v));
  if (!all.length) return;
  const vMin = Math.min(...all), vMax = Math.max(...all);
  const dMax = Math.max(...depths, 1);
  const padL = 34, padR = 10, padT = 12, padB = 10;
  const plotW = w - padL - padR, plotH = h - padT - padB;

  ctx.strokeStyle = '#1c344d';
  ctx.fillStyle = '#7892aa';
  ctx.font = '9px system-ui';
  for (let i = 0; i < 5; i++) {
    const y = padT + (i * plotH) / 4;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
  }
  ctx.fillText(unit, 4, 10);

  const xOf = (v) => padL + ((v - vMin) / ((vMax - vMin) || 1)) * plotW;
  const yOf = (d) => padT + (d / dMax) * plotH;

  function plot(series, color) {
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    depths.forEach((d, i) => {
      const v = series[i];
      if (!Number.isFinite(v)) return;
      const x = xOf(v), y = yOf(d);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  }
  plot(seriesA, colorA);
  plot(seriesB, colorB);

  ctx.fillStyle = '#8ea6bf';
  depths.forEach((d) => { if (d === 0 || d === depths[depths.length - 1] || d === depths[Math.floor(depths.length / 2)]) ctx.fillText(`${d}m`, 2, yOf(d) + 3); });
}

// ---------------------------------------------------------------------------
// Live coordinates (raycast onto sea-level plane)
// ---------------------------------------------------------------------------
const raySurface = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
const hitPoint = new THREE.Vector3();

function attachMouseHandlers() {
  renderer.domElement.addEventListener('mousemove', onMouseMove);
  renderer.domElement.addEventListener('click', onClick);
}
function onMouseMove(e) {
  const r = renderer.domElement.getBoundingClientRect();
  mouse.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  mouse.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);
  if (raycaster.ray.intersectPlane(raySurface, hitPoint)) {
    const { lon, lat } = fromXZ(hitPoint.x, hitPoint.z);
    const ns = lat >= 0 ? 'N' : 'S';
    const ew = lon >= 0 ? 'E' : 'W';
    $('liveCoords').textContent = `${Math.abs(lat).toFixed(2)}°${ns}, ${Math.abs(lon).toFixed(2)}°${ew}`;
  }
}
function onClick() {
  raycaster.setFromCamera(mouse, camera);
  const hit = raycaster.intersectObjects(obsGroup.children)[0];
  if (hit) selectObs(hit.object.userData);
}

// ---------------------------------------------------------------------------
// Orchestration
// ---------------------------------------------------------------------------
async function refreshAll() {
  await renderFieldLayers();
  await renderCurrents();
  if ($('isoToggle').checked) await renderIsosurface();
}

function wireControls() {
  document.querySelectorAll('.layerToggle').forEach((el) => el.addEventListener('change', () => { refreshAll(); }));
  $('currentsToggle').onchange = renderCurrents;
  $('modelToggle').onchange = refreshAll;
  $('insituToggle').onchange = () => renderObsMarkers(document.querySelector('#filters button.active')?.dataset.type || 'all');

  $('isoToggle').onchange = async () => { if ($('isoToggle').checked) await primeIsoThreshold(); renderIsosurface(); };
  $('isoThreshold').oninput = renderIsosurface;

  $('palette').onchange = () => refreshAll();
  $('logScale').onchange = () => refreshAll();

  $('time').oninput = () => { clearFieldCache(); refreshAll(); if (selectedObs) selectObs(selectedObs); };
  $('depth').oninput = () => { renderFieldLayers(); renderCurrents(); };
  $('opacity').oninput = () => { $('opacityVal').textContent = opacityVal().toFixed(2); renderFieldLayers(); };
  $('vertical').oninput = () => { $('verticalVal').textContent = `${verticalExaggeration().toFixed(1)}×`; refreshAll(); if (selectedObs) selectObs(selectedObs); };

  $('prevTime').onclick = () => { $('time').value = Math.max(0, timeIndex() - 1); $('time').dispatchEvent(new Event('input')); };
  $('nextTime').onclick = () => { $('time').value = Math.min(parseInt($('time').max, 10), timeIndex() + 1); $('time').dispatchEvent(new Event('input')); };
  $('play').onclick = () => {
    playing = !playing;
    $('play').textContent = playing ? '❚❚ Pause' : '▶ Play';
    if (playing) {
      playTimer = setInterval(() => {
        const max = parseInt($('time').max, 10);
        $('time').value = (timeIndex() + 1) % (max + 1);
        $('time').dispatchEvent(new Event('input'));
      }, 1400);
    } else clearInterval(playTimer);
  };

  $('resetView').onclick = () => {
    camera.position.set(26, 20, 34);
    controls.target.set(0, -3, 0);
    controls.update();
    $('depth').value = 0; $('opacity').value = 0.75; $('vertical').value = 2.2;
    $('opacityVal').textContent = '0.75'; $('verticalVal').textContent = '2.2×';
    refreshAll();
  };

  $('fullscreenBtn').onclick = () => {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
    else document.exitFullscreen?.();
  };

  document.querySelectorAll('#filters button').forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll('#filters button').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      renderObsMarkers(b.dataset.type);
    };
  });
}

async function boot() {
  META = await fetch('/api/metadata').then((r) => r.json());
  fitMapping();
  $('time').max = META.times.length - 1;
  $('depth').max = META.depths.length - 1;

  const health = await fetch('/api/health').then((r) => r.json());
  $('status').textContent = health.netcdf_active ? '● LIVE NETCDF DATA' : '● DEMO DATA';
  $('stModel').textContent = 'READY';
  $('stObs').textContent = 'READY';
  $('stNetcdf').textContent = health.netcdf_active ? 'ACTIVE' : 'DEMO FALLBACK';
  $('stApi').textContent = 'ONLINE';

  OBS = await fetch('/api/observations').then((r) => r.json());
  renderObsMarkers();
  attachMouseHandlers();
  wireControls();
  await refreshAll();
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}
animate();
boot();
