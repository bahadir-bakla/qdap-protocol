import { motion, useScroll, useTransform } from "framer-motion";
import {
  Activity,
  ArrowRight,
  BadgeCheck,
  Blocks,
  Braces,
  Cpu,
  Gauge,
  Github,
  LockKeyhole,
  Network,
  PackageCheck,
  Radio,
  RadioTower,
  ShieldCheck,
  SignalHigh,
  Terminal,
  Zap,
  Layers
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

type ProtocolMetric = {
  name: string;
  emergency: number;
  total: number;
  latency: string;
  tone: "qdap" | "neutral" | "warn";
};

type Feature = {
  icon: React.ElementType;
  label: string;
  value: string;
  detail: string;
};

const crisisMetrics: ProtocolMetric[] = [
  { name: "QDAP", emergency: 99, total: 73.5, latency: "<500ms: 99% ★ real WAN", tone: "qdap" },
  { name: "HTTP/1.1", emergency: 68.8, total: 99.5, latency: "31% miss 500ms deadline", tone: "neutral" },
  { name: "WebSocket", emergency: 66, total: 100, latency: "HOL blocks emergency msgs", tone: "neutral" },
  { name: "HTTP/2", emergency: 55, total: 100, latency: "worst deadline under crisis", tone: "warn" }
];

const features: Feature[] = [
  {
    icon: Gauge,
    label: "Throughput",
    value: "7.7x",
    detail: "Parallel sender keeps streams full without waiting for serial ACK rhythm."
  },
  {
    icon: PackageCheck,
    label: "Emergency delivery",
    value: "100%",
    detail: "Adaptive FEC gives crisis messages multiple recovery paths under 35% loss."
  },
  {
    icon: Radio,
    label: "ACK overhead",
    value: "~0",
    detail: "Ghost Session tracks delivery state implicitly and frees the hot path."
  },
  {
    icon: ShieldCheck,
    label: "Security core",
    value: "A+",
    detail: "X25519 session setup, AES-256-GCM frames, rotation, and Rust acceleration."
  }
];

const layers = [
  {
    idx: "01",
    title: "QFT Scheduler",
    tag: "Adaptive chunk selection",
    formula: "score_i = ln(w_i) + Σ channel_signals",
    text: "Log-linear softmax over 5 chunk strategies (4KB→1MB). Learns from each decision — RTT, loss, and payload size steer the weights in real time.",
    metric: "374k decisions/s",
    icon: Network,
    color: "cyan"
  },
  {
    idx: "02",
    title: "Ghost Session",
    tag: "Zero-ACK delivery tracking",
    formula: "sig = HMAC-SHA256(key, seq‖payload[:32])[:8]",
    text: "Both ends share a deterministic ghost state. The sender never waits for an explicit ACK — implicit state collapse detects loss at 2.5× expected RTT.",
    metric: "~0 ACK overhead",
    icon: Activity,
    color: "green"
  },
  {
    idx: "03",
    title: "Adaptive FEC",
    tag: "XOR systematic (k,r) code",
    formula: "p_eff = Σ_{i=r+1}^{n} C(n,i)·pⁱ·(1-p)ⁿ⁻ⁱ",
    text: "Emergency profile (k=1, r=2): any 1-of-3 copies recovers the message. At 35% loss, effective loss drops to 0.35³ = 4.3%. Profiles adapt to channel conditions.",
    metric: "8.16× FEC improvement",
    icon: Blocks,
    color: "amber"
  },
  {
    idx: "04",
    title: "Delta Encoder",
    tag: "Binary frame diff protocol",
    formula: "[0x01][bitmask:16][changed_fields_msgpack]",
    text: "First frame is a full snapshot. Subsequent frames carry only changed fields with a 16-bit bitmask. IoT sensors with slow-moving data shrink by 74.4%.",
    metric: "74.4% size reduction",
    icon: Braces,
    color: "cyan"
  },
  {
    idx: "05",
    title: "Parallel Sender",
    tag: "8-stream concurrent pipeline",
    formula: "throughput = min(Σ streams, BDP limit)",
    text: "Eight concurrent streams keep the bandwidth-delay product saturated without head-of-line blocking. Each stream is independently paced and loss-aware.",
    metric: "7.7× speedup",
    icon: Layers,
    color: "green"
  }
];

const perfMatrix = [
  {
    label: "Emergency delivery (<500ms)",
    qdap: "99%",
    next: "68.8%",
    nextLabel: "HTTP/1.1",
    scenario: "Real AWS WAN · crisis 30% loss",
    icon: Zap,
    highlight: true,
    nextPct: 69,
    badge: "1.44× higher"
  },
  {
    label: "Large file transfer (10MB)",
    qdap: "100% success · 76.7 Mbps",
    next: "100% · 0.05 Mbps",
    nextLabel: "HTTP/1.1",
    scenario: "Real AWS WAN · Ireland↔Singapore",
    icon: Gauge,
    highlight: true,
    nextPct: 1,
    badge: "99× higher throughput"
  },
  {
    label: "Bulk throughput (10MB)",
    qdap: "127 Mbps",
    next: "49 Mbps",
    nextLabel: "HTTP/3",
    scenario: "Normal 20ms / 1% loss",
    icon: Layers,
    highlight: false,
    nextPct: 39,
    badge: "2.6× faster"
  },
  {
    label: "LAN throughput (IoT / factory)",
    qdap: "20.8 Mbps",
    next: "0.39 Mbps",
    nextLabel: "gRPC",
    scenario: "Real WiFi LAN · two physical machines",
    icon: RadioTower,
    highlight: true,
    nextPct: 2,
    badge: "53× higher throughput"
  },
  {
    label: "LAN p99 latency (50ms deadline)",
    qdap: "21.4ms · 99.8% on time",
    next: "230ms · 92.6% on time",
    nextLabel: "gRPC",
    scenario: "Real WiFi LAN · two physical machines",
    icon: Activity,
    highlight: false,
    nextPct: 9,
    badge: "10.7× lower latency"
  },
  {
    label: "Frame compression",
    qdap: "74.4%",
    next: "0%",
    nextLabel: "HTTP/3",
    scenario: "IoT sensor delta stream",
    icon: Braces,
    highlight: false,
    nextPct: 2,
    badge: "74.4% vs 0% savings"
  }
];

const iotMetrics = [
  { scenario: "WiFi LAN · real HW (★)", qdap: 99.8, mqtt: 0.0, coap: 98.2, amqp: 92.6 },
  { scenario: "Normal 20ms/0.5%", qdap: 100.0, mqtt: 98.7, coap: 100.0, amqp: 99.3 },
  { scenario: "Mobile 4G 80ms/5%", qdap: 100.0, mqtt: 93.0, coap: 100.0, amqp: 90.3 },
  { scenario: "High loss 150ms/20%", qdap: 96.9, mqtt: 66.7, coap: 100.0, amqp: 73.2 },
  { scenario: "Crisis 300ms/35%", qdap: 100.0, mqtt: 60.0, coap: 96.7, amqp: 48.3 },
];

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function calculateLiveMetrics(loss: number, rtt: number) {
  const qdapDelivery = clamp(99.4 - Math.max(0, loss - 35) * 0.24 - Math.max(0, rtt - 300) * 0.018, 88, 100);
  const baselineDelivery = clamp(93 - loss * 0.75 - Math.max(0, rtt - 80) * 0.035, 18, 91);
  const throughput = clamp(14.2 - loss * 0.12 - rtt * 0.006, 5.2, 14.2);
  const ackSaved = clamp(37 + loss * 0.72 + rtt * 0.015, 36, 74);
  return { qdapDelivery, baselineDelivery, throughput, ackSaved, advantage: qdapDelivery - baselineDelivery };
}

function latLonToVec3(lat: number, lon: number, r = 1): THREE.Vector3 {
  const phi = lat * (Math.PI / 180);
  const theta = lon * (Math.PI / 180);
  return new THREE.Vector3(
    r * Math.cos(phi) * Math.sin(theta),
    r * Math.sin(phi),
    r * Math.cos(phi) * Math.cos(theta)
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// HERO 3D scene — bloom enabled
// ──────────────────────────────────────────────────────────────────────────────
function ProtocolField({ loss, rtt }: { loss: number; rtt: number }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const stateRef = useRef({ loss, rtt });

  useEffect(() => {
    stateRef.current = { loss, rtt };
  }, [loss, rtt]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x070b10, 0.018);

    const camera = new THREE.PerspectiveCamera(44, mount.clientWidth / mount.clientHeight, 0.1, 160);
    camera.position.set(0, 7.8, 18.5);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x05070a, 1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ReinhardToneMapping;
    renderer.toneMappingExposure = 1.2;
    mount.appendChild(renderer.domElement);

    // Bloom post-processing
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(mount.clientWidth, mount.clientHeight),
      1.6, 0.55, 0.08
    );
    composer.addPass(bloomPass);

    const ambient = new THREE.AmbientLight(0xa7c9ff, 0.72);
    scene.add(ambient);
    const key = new THREE.PointLight(0x59f5d4, 120, 48);
    key.position.set(-7, 9, 8);
    scene.add(key);
    const alertLight = new THREE.PointLight(0xffb05c, 95, 38);
    alertLight.position.set(6, 4, -4);
    scene.add(alertLight);

    const grid = new THREE.GridHelper(36, 36, 0x2d4d55, 0x101a22);
    grid.position.y = -2.65;
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.28;
    scene.add(grid);

    const starPositions = new Float32Array(900 * 3);
    const starColors = new Float32Array(900 * 3);
    const cyan = new THREE.Color(0x58f5d3);
    const amber = new THREE.Color(0xffbd67);
    const steel = new THREE.Color(0x7f9bac);
    for (let i = 0; i < 900; i++) {
      starPositions[i * 3] = (Math.random() - 0.5) * 58;
      starPositions[i * 3 + 1] = Math.random() * 22 - 3;
      starPositions[i * 3 + 2] = (Math.random() - 0.5) * 44;
      const color = i % 11 === 0 ? amber : i % 3 === 0 ? cyan : steel;
      starColors[i * 3] = color.r;
      starColors[i * 3 + 1] = color.g;
      starColors[i * 3 + 2] = color.b;
    }
    const starGeometry = new THREE.BufferGeometry();
    starGeometry.setAttribute("position", new THREE.BufferAttribute(starPositions, 3));
    starGeometry.setAttribute("color", new THREE.BufferAttribute(starColors, 3));
    const stars = new THREE.Points(starGeometry, new THREE.PointsMaterial({
      size: 0.055, vertexColors: true, transparent: true, opacity: 0.85,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    scene.add(stars);

    const nodeMaterial = new THREE.MeshStandardMaterial({
      color: 0xd7e7ef, emissive: 0x2c5c66, metalness: 0.65, roughness: 0.28
    });
    const qdapMaterial = new THREE.MeshStandardMaterial({
      color: 0x59f5d4, emissive: 0x18e8c4, metalness: 0.4, roughness: 0.22
    });
    const warnMaterial = new THREE.MeshStandardMaterial({
      color: 0xffb15c, emissive: 0xd45010, metalness: 0.3, roughness: 0.18
    });

    const nodeGeometry = new THREE.IcosahedronGeometry(0.34, 1);
    const qdapHub = new THREE.Mesh(new THREE.IcosahedronGeometry(0.82, 3), qdapMaterial);
    qdapHub.position.set(0, 0.1, 0);
    scene.add(qdapHub);

    const hubHalo = new THREE.Mesh(
      new THREE.TorusGeometry(1.25, 0.022, 8, 160),
      new THREE.MeshBasicMaterial({
        color: 0x58f5d3, transparent: true, opacity: 0.9,
        blending: THREE.AdditiveBlending
      })
    );
    hubHalo.rotation.x = Math.PI / 2;
    qdapHub.add(hubHalo);

    const nodePositions = [
      [-8.8, -0.2, -4.8], [-6.8, 1.8, 2.8], [-2.8, -0.6, 6.4],
      [2.8, 1.6, 5.8], [8.6, -0.1, 2], [8.8, 2.2, -4.4],
      [3.8, -1.1, -7], [-4.4, 0.9, -6.8]
    ].map(([x, y, z]) => new THREE.Vector3(x, y, z));

    const nodes = nodePositions.map((position, index) => {
      const mesh = new THREE.Mesh(nodeGeometry, index === 4 ? warnMaterial : nodeMaterial);
      mesh.position.copy(position);
      scene.add(mesh);
      return mesh;
    });

    const routeObjects = nodes.map((node, index) => {
      const start = new THREE.Vector3(0, 0.1, 0);
      const end = node.position.clone();
      const mid = start.clone().lerp(end, 0.48);
      mid.y += 2.2 + (index % 3) * 0.55;
      mid.x += Math.sin(index * 1.9) * 0.8;
      const curve = new THREE.CatmullRomCurve3([start, mid, end]);
      const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(96));
      const base = new THREE.Line(geometry, new THREE.LineBasicMaterial({
        color: index % 2 === 0 ? 0x58f5d3 : 0x7392a5, transparent: true,
        opacity: index % 2 === 0 ? 0.35 : 0.20, blending: THREE.AdditiveBlending, depthWrite: false
      }));
      const glow = new THREE.Line(geometry.clone(), new THREE.LineBasicMaterial({
        color: index % 3 === 0 ? 0xffbd67 : 0x58f5d3, transparent: true,
        opacity: 0.12, blending: THREE.AdditiveBlending, depthWrite: false
      }));
      scene.add(base);
      scene.add(glow);
      return { curve, base, glow };
    });

    const packetGeometry = new THREE.SphereGeometry(0.1, 14, 14);
    const emergencyGeometry = new THREE.SphereGeometry(0.14, 18, 18);
    const normalMaterial = new THREE.MeshBasicMaterial({
      color: 0x9cc8e3, transparent: true, opacity: 0.92,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    const emergencyMaterial = new THREE.MeshBasicMaterial({
      color: 0xffbd67, transparent: true, opacity: 1,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    const successMaterial = new THREE.MeshBasicMaterial({
      color: 0x58f5d3, transparent: true, opacity: 1,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    const droppedMaterial = new THREE.MeshBasicMaterial({
      color: 0xff5f57, transparent: true, opacity: 0.86,
      blending: THREE.AdditiveBlending, depthWrite: false
    });

    const packets = Array.from({ length: 76 }, (_, i) => {
      const emergency = i % 13 === 0;
      const group = new THREE.Group();
      const beadCount = emergency ? 3 : 1;
      const beads = Array.from({ length: beadCount }, (_, beadIndex) => {
        const bead = new THREE.Mesh(
          emergency ? emergencyGeometry : packetGeometry,
          emergency ? emergencyMaterial : normalMaterial
        );
        bead.position.set((beadIndex - 1) * 0.22, emergency ? Math.sin(beadIndex * 2.1) * 0.1 : 0, 0);
        group.add(bead);
        return bead;
      });
      const trailPositions = new Float32Array(16 * 3);
      const trailGeometry = new THREE.BufferGeometry();
      trailGeometry.setAttribute("position", new THREE.BufferAttribute(trailPositions, 3));
      const trail = new THREE.Line(trailGeometry, new THREE.LineBasicMaterial({
        color: emergency ? 0xffbd67 : 0x58f5d3, transparent: true,
        opacity: emergency ? 0.72 : 0.3, blending: THREE.AdditiveBlending, depthWrite: false
      }));
      scene.add(trail);
      scene.add(group);
      return {
        group, beads, trail, trailPositions,
        lane: (i * 3) % nodePositions.length,
        offset: Math.random(),
        speed: emergency ? 0.32 + Math.random() * 0.1 : 0.16 + Math.random() * 0.11,
        emergency, phase: Math.random() * Math.PI * 2
      };
    });

    const shockwaves = Array.from({ length: 5 }, (_, i) => {
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(1, 0.020, 8, 180),
        new THREE.MeshBasicMaterial({
          color: i % 2 === 0 ? 0x58f5d3 : 0xffbd67, transparent: true,
          opacity: 0.22, blending: THREE.AdditiveBlending, depthWrite: false
        })
      );
      ring.rotation.x = Math.PI / 2;
      ring.position.y = -2.45;
      scene.add(ring);
      return ring;
    });

    const dataColumns = Array.from({ length: 42 }, (_, i) => {
      const bar = new THREE.Mesh(
        new THREE.BoxGeometry(0.035, 1, 0.035),
        new THREE.MeshBasicMaterial({
          color: i % 5 === 0 ? 0xffbd67 : 0x58f5d3, transparent: true,
          opacity: 0.24, blending: THREE.AdditiveBlending, depthWrite: false
        })
      );
      bar.position.set((Math.random() - 0.5) * 24, -1.8, (Math.random() - 0.5) * 18);
      bar.scale.y = 0.2 + Math.random() * 1.4;
      scene.add(bar);
      return bar;
    });

    let frame = 0;
    let animationId = 0;
    const clock = new THREE.Clock();

    const resize = () => {
      if (!mount) return;
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
      composer.setSize(mount.clientWidth, mount.clientHeight);
      bloomPass.resolution.set(mount.clientWidth, mount.clientHeight);
    };

    const animate = () => {
      const elapsed = clock.getElapsedTime();
      const current = stateRef.current;
      const lossPressure = current.loss / 50;
      const rttDrag = current.rtt / 500;
      const throughputPulse = 1.2 - Math.min(0.75, rttDrag * 0.55 + lossPressure * 0.26);

      stars.rotation.y += 0.0006;
      stars.position.z = Math.sin(elapsed * 0.2) * 0.5;

      qdapHub.rotation.y += 0.012;
      qdapHub.rotation.x = Math.sin(elapsed * 0.65) * 0.18;
      qdapHub.scale.setScalar(1.08 + Math.sin(elapsed * 2.9) * 0.055 + throughputPulse * 0.035);
      hubHalo.rotation.z -= 0.018;
      hubHalo.scale.setScalar(1 + Math.sin(elapsed * 3.6) * 0.08);
      (hubHalo.material as THREE.MeshBasicMaterial).opacity = 0.52 + Math.sin(elapsed * 4.4) * 0.22;

      nodes.forEach((node, index) => {
        node.rotation.x += 0.005 + index * 0.0006;
        node.rotation.y += 0.01;
        node.position.y = nodePositions[index].y + Math.sin(elapsed * 0.9 + index) * 0.22;
        node.scale.setScalar(1 + Math.sin(elapsed * 2.1 + index) * 0.06);
      });

      shockwaves.forEach((ring, index) => {
        ring.rotation.z += 0.006 + index * 0.0016;
        const cycle = (elapsed * (0.12 + index * 0.018) + index * 0.2) % 1;
        const scale = 1.4 + cycle * (5.2 + lossPressure * 2.2);
        ring.scale.set(scale, scale, scale);
        (ring.material as THREE.MeshBasicMaterial).opacity = (1 - cycle) * (0.36 + lossPressure * 0.24);
      });

      routeObjects.forEach(({ base, glow }, index) => {
        (base.material as THREE.LineBasicMaterial).opacity = index % 2 === 0
          ? 0.28 + throughputPulse * 0.18 : 0.14 + rttDrag * 0.1;
        (glow.material as THREE.LineBasicMaterial).opacity =
          0.1 + Math.sin(elapsed * 2.4 + index) * 0.05 + (index % 3 === 0 ? lossPressure * 0.18 : 0);
      });

      dataColumns.forEach((bar, index) => {
        bar.position.y = -2.45 + ((elapsed * (0.6 + index * 0.01) + index * 0.13) % 1) * 4.2;
        bar.scale.y = 0.3 + Math.sin(elapsed * 2.1 + index) * 0.2 + throughputPulse * 1.6;
        (bar.material as THREE.MeshBasicMaterial).opacity = 0.1 + throughputPulse * 0.18;
      });

      packets.forEach((packet) => {
        const route = routeObjects[packet.lane];
        const speedPenalty = packet.emergency ? 0.01 : rttDrag * 0.09 + lossPressure * 0.035;
        packet.offset = (packet.offset + Math.max(0.045, packet.speed * throughputPulse - speedPenalty) * 0.012) % 1;
        const point = route.curve.getPointAt(packet.offset);
        point.y += Math.sin(packet.offset * Math.PI + packet.phase) * (packet.emergency ? 0.38 : 0.18);
        packet.group.position.copy(point);

        const dropped = !packet.emergency && Math.sin(frame * 0.021 + packet.phase + packet.lane * 1.3) < -0.88 + lossPressure * 0.42;
        packet.group.visible = !dropped || packet.offset < 0.16;
        const scale = packet.emergency ? 1.45 + lossPressure * 0.55 : 0.78 + throughputPulse * 0.45;
        packet.group.scale.setScalar(scale);
        packet.group.rotation.z += packet.emergency ? 0.08 : 0.025;
        packet.group.rotation.y += 0.045;

        packet.beads.forEach((bead, beadIndex) => {
          bead.material = dropped ? droppedMaterial
            : packet.emergency && packet.offset > 0.68 ? successMaterial
            : packet.emergency ? emergencyMaterial : normalMaterial;
          bead.position.x = packet.emergency ? Math.sin(elapsed * 4.2 + beadIndex * 2.1) * 0.23 : 0;
          bead.position.y = packet.emergency ? Math.cos(elapsed * 3.8 + beadIndex * 1.8) * 0.12 : 0;
        });

        for (let i = 15; i > 0; i--) {
          packet.trailPositions[i * 3] = packet.trailPositions[(i - 1) * 3];
          packet.trailPositions[i * 3 + 1] = packet.trailPositions[(i - 1) * 3 + 1];
          packet.trailPositions[i * 3 + 2] = packet.trailPositions[(i - 1) * 3 + 2];
        }
        packet.trailPositions[0] = point.x;
        packet.trailPositions[1] = point.y;
        packet.trailPositions[2] = point.z;
        (packet.trail.geometry.attributes.position as THREE.BufferAttribute).needsUpdate = true;
        (packet.trail.material as THREE.LineBasicMaterial).opacity = packet.group.visible
          ? (packet.emergency ? 0.82 : 0.26 + throughputPulse * 0.14) : 0.04;
      });

      camera.position.x = Math.sin(elapsed * 0.18) * 2.2;
      camera.position.y = 7.4 + Math.sin(elapsed * 0.26) * 0.46;
      camera.position.z = 18 + Math.cos(elapsed * 0.14) * 1.25;
      camera.lookAt(0, -0.2, 0);

      frame++;
      composer.render();
      animationId = requestAnimationFrame(animate);
    };

    window.addEventListener("resize", resize);
    animate();

    return () => {
      cancelAnimationFrame(animationId);
      window.removeEventListener("resize", resize);
      mount.removeChild(renderer.domElement);
      renderer.dispose();
      nodeGeometry.dispose();
      packetGeometry.dispose();
      emergencyGeometry.dispose();
      starGeometry.dispose();
      routeObjects.forEach(({ base, glow }) => { base.geometry.dispose(); glow.geometry.dispose(); });
      packets.forEach((packet) => packet.trail.geometry.dispose());
    };
  }, []);

  return <div className="protocol-field" ref={mountRef} aria-label="Animated QDAP network simulation" />;
}

// ──────────────────────────────────────────────────────────────────────────────
// WAN GLOBE — Ireland → Singapore with bloom
// ──────────────────────────────────────────────────────────────────────────────
function WANGlobe() {
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, mount.clientWidth / mount.clientHeight, 0.1, 80);
    camera.position.set(0, 0.4, 3.6);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x030609, 1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ReinhardToneMapping;
    renderer.toneMappingExposure = 1.4;
    mount.appendChild(renderer.domElement);

    // Globe bloom
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(mount.clientWidth, mount.clientHeight),
      2.2, 0.7, 0.05
    );
    composer.addPass(bloomPass);

    // Stars background
    const starPos = new Float32Array(1400 * 3);
    for (let i = 0; i < 1400 * 3; i++) starPos[i] = (Math.random() - 0.5) * 40;
    const starGeo = new THREE.BufferGeometry();
    starGeo.setAttribute("position", new THREE.BufferAttribute(starPos, 3));
    scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({
      size: 0.038, color: 0x8ab8d0, transparent: true, opacity: 0.55,
      blending: THREE.AdditiveBlending, depthWrite: false
    })));

    // Globe group — everything inside rotates together
    const globeGroup = new THREE.Group();
    // Orient so Ireland is left, Singapore right, arc visible on load
    globeGroup.rotation.y = -38 * Math.PI / 180;
    scene.add(globeGroup);

    // Earth sphere
    const globeGeo = new THREE.SphereGeometry(1, 72, 72);
    const globeMat = new THREE.MeshPhongMaterial({
      color: 0x0a1520,
      emissive: 0x061018,
      shininess: 8,
      specular: 0x1a4060,
    });
    globeGroup.add(new THREE.Mesh(globeGeo, globeMat));

    // Atmosphere glow
    const atmoGeo = new THREE.SphereGeometry(1.06, 32, 32);
    const atmoMat = new THREE.MeshBasicMaterial({
      color: 0x1a8aaa, transparent: true, opacity: 0.08,
      side: THREE.BackSide, blending: THREE.AdditiveBlending, depthWrite: false
    });
    globeGroup.add(new THREE.Mesh(atmoGeo, atmoMat));

    // Outer haze
    const hazeGeo = new THREE.SphereGeometry(1.12, 32, 32);
    const hazeMat = new THREE.MeshBasicMaterial({
      color: 0x0d5578, transparent: true, opacity: 0.035,
      side: THREE.BackSide, blending: THREE.AdditiveBlending, depthWrite: false
    });
    globeGroup.add(new THREE.Mesh(hazeGeo, hazeMat));

    // Lat/lon grid
    const gridMat = new THREE.LineBasicMaterial({
      color: 0x1e4a5c, transparent: true, opacity: 0.38,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    const denseGridMat = new THREE.LineBasicMaterial({
      color: 0x0e2a38, transparent: true, opacity: 0.22,
      blending: THREE.AdditiveBlending, depthWrite: false
    });

    for (let lat = -80; lat <= 80; lat += 20) {
      const phi = lat * (Math.PI / 180);
      const r = Math.cos(phi);
      const y = Math.sin(phi);
      const pts: THREE.Vector3[] = [];
      for (let i = 0; i <= 80; i++) {
        const theta = (i / 80) * Math.PI * 2;
        pts.push(new THREE.Vector3(r * Math.sin(theta), y, r * Math.cos(theta)));
      }
      globeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), gridMat));
    }
    // Extra dense latitude lines near route
    for (let lat = -80; lat <= 80; lat += 10) {
      if (lat % 20 === 0) continue;
      const phi = lat * (Math.PI / 180);
      const r = Math.cos(phi);
      const y = Math.sin(phi);
      const pts: THREE.Vector3[] = [];
      for (let i = 0; i <= 80; i++) {
        const theta = (i / 80) * Math.PI * 2;
        pts.push(new THREE.Vector3(r * Math.sin(theta), y, r * Math.cos(theta)));
      }
      globeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), denseGridMat));
    }

    for (let lon = 0; lon < 360; lon += 20) {
      const theta = lon * (Math.PI / 180);
      const pts: THREE.Vector3[] = [];
      for (let i = 0; i <= 80; i++) {
        const phi2 = ((i / 80) - 0.5) * Math.PI;
        pts.push(new THREE.Vector3(Math.cos(phi2) * Math.sin(theta), Math.sin(phi2), Math.cos(phi2) * Math.cos(theta)));
      }
      globeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lon % 40 === 0 ? gridMat : denseGridMat));
    }

    // City positions (on globe surface)
    const dublinPos = latLonToVec3(53.35, -6.26, 1.02);
    const singaporePos = latLonToVec3(1.35, 103.82, 1.02);

    // City dot + pulse ring
    function addCityMarker(pos: THREE.Vector3, color: number, label: string) {
      const dotGeo = new THREE.SphereGeometry(0.028, 16, 16);
      const dotMat = new THREE.MeshBasicMaterial({ color, blending: THREE.AdditiveBlending });
      const dot = new THREE.Mesh(dotGeo, dotMat);
      dot.position.copy(pos);
      globeGroup.add(dot);

      // Spike upward from surface
      const spikeGeo = new THREE.CylinderGeometry(0.004, 0.004, 0.12, 8);
      const spikeMat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending });
      const spike = new THREE.Mesh(spikeGeo, spikeMat);
      spike.position.copy(pos.clone().normalize().multiplyScalar(1.08));
      spike.lookAt(0, 0, 0);
      spike.rotateX(Math.PI / 2);
      globeGroup.add(spike);

      // Pulse rings (3 concentric, phase-offset)
      const rings = Array.from({ length: 3 }, (_, i) => {
        const ringGeo = new THREE.RingGeometry(0.04 + i * 0.002, 0.056 + i * 0.002, 32);
        const ringMat = new THREE.MeshBasicMaterial({
          color, transparent: true, opacity: 0.7,
          side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false
        });
        const ring = new THREE.Mesh(ringGeo, ringMat);
        ring.position.copy(pos);
        ring.lookAt(new THREE.Vector3(0, 0, 0));
        globeGroup.add(ring);
        return { ring, mat: ringMat, phaseOffset: i * 0.5 };
      });

      void label;
      return { dot, rings };
    }

    const dublinMarker = addCityMarker(dublinPos, 0x58f5d3, "Dublin");
    const singaporeMarker = addCityMarker(singaporePos, 0xffbd67, "Singapore");

    // Great-circle arc via real geographic waypoints (Dublin→Scandinavia→Caspian→India→Singapore)
    const AH = 1.22;
    const arcCurve = new THREE.CatmullRomCurve3([
      dublinPos.clone(),
      latLonToVec3(62, 18, AH),   // Sweden
      latLonToVec3(50, 42, AH),   // Caspian Sea
      latLonToVec3(22, 78, AH),   // Central India
      latLonToVec3(8, 98, AH),    // Gulf of Thailand
      singaporePos.clone(),
    ]);
    const arcPts = arcCurve.getPoints(160);
    const arcGeo = new THREE.BufferGeometry().setFromPoints(arcPts);

    // Multi-layer arc: base + bright core
    const arcBase = new THREE.Line(arcGeo, new THREE.LineBasicMaterial({
      color: 0x1a8888, transparent: true, opacity: 0.5,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    globeGroup.add(arcBase);
    const arcCore = new THREE.Line(arcGeo.clone(), new THREE.LineBasicMaterial({
      color: 0x58f5d3, transparent: true, opacity: 0.9,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    globeGroup.add(arcCore);

    // Packet on arc
    const packetGeo = new THREE.SphereGeometry(0.038, 16, 16);
    const packetMat = new THREE.MeshBasicMaterial({
      color: 0xffee88, blending: THREE.AdditiveBlending, depthWrite: false
    });
    const packet = new THREE.Mesh(packetGeo, packetMat);
    globeGroup.add(packet);

    // Packet trail
    const TRAIL_LEN = 24;
    const trailPos = new Float32Array(TRAIL_LEN * 3);
    const trailGeo = new THREE.BufferGeometry();
    trailGeo.setAttribute("position", new THREE.BufferAttribute(trailPos, 3));
    const trailLine = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({
      color: 0xffbd67, transparent: true, opacity: 0.7,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    globeGroup.add(trailLine);

    // Second slower packet (opposite direction)
    const packet2Geo = new THREE.SphereGeometry(0.025, 12, 12);
    const packet2Mat = new THREE.MeshBasicMaterial({
      color: 0x58f5d3, blending: THREE.AdditiveBlending, depthWrite: false
    });
    const packet2 = new THREE.Mesh(packet2Geo, packet2Mat);
    globeGroup.add(packet2);

    // Lights
    scene.add(new THREE.AmbientLight(0x223344, 2.0));
    const sunLight = new THREE.PointLight(0x6aaecc, 4, 14);
    sunLight.position.set(4, 2.5, 3.5);
    scene.add(sunLight);
    const backLight = new THREE.PointLight(0x1a4466, 1.5, 10);
    backLight.position.set(-3, -1, -4);
    scene.add(backLight);

    let packetT = 0;
    let packet2T = 0.5;
    let animId = 0;
    const clock = new THREE.Clock();

    const resize = () => {
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
      composer.setSize(mount.clientWidth, mount.clientHeight);
      bloomPass.resolution.set(mount.clientWidth, mount.clientHeight);
    };

    const animate = () => {
      const elapsed = clock.getElapsedTime();

      // Slow globe rotation — gentle drift, not a spin
      globeGroup.rotation.y += 0.0005;

      // Packet 1 (forward: Dublin → Singapore)
      packetT = (packetT + 0.003) % 1;
      const p1 = arcCurve.getPointAt(packetT);
      packet.position.copy(p1);
      packet.scale.setScalar(1 + Math.sin(elapsed * 1.8) * 0.08);

      // Trail
      for (let i = TRAIL_LEN - 1; i > 0; i--) {
        trailPos[i * 3] = trailPos[(i - 1) * 3];
        trailPos[i * 3 + 1] = trailPos[(i - 1) * 3 + 1];
        trailPos[i * 3 + 2] = trailPos[(i - 1) * 3 + 2];
      }
      trailPos[0] = p1.x;
      trailPos[1] = p1.y;
      trailPos[2] = p1.z;
      (trailGeo.attributes.position as THREE.BufferAttribute).needsUpdate = true;

      // Packet 2 (reverse: Singapore → Dublin)
      packet2T = (packet2T + 0.002) % 1;
      packet2.position.copy(arcCurve.getPointAt(1 - packet2T));
      packet2.scale.setScalar(1 + Math.sin(elapsed * 1.4 + Math.PI) * 0.07);

      // City pulse rings
      [...dublinMarker.rings, ...singaporeMarker.rings].forEach(({ ring, mat, phaseOffset }) => {
        const cycle = (elapsed * 0.9 + phaseOffset) % 1;
        ring.scale.setScalar(1 + cycle * 3.5);
        mat.opacity = (1 - cycle) * 0.65;
      });

      // Subtle camera drift
      camera.position.x = Math.sin(elapsed * 0.12) * 0.18;
      camera.position.y = 0.4 + Math.sin(elapsed * 0.09) * 0.12;
      camera.lookAt(0, 0, 0);

      composer.render();
      animId = requestAnimationFrame(animate);
    };

    window.addEventListener("resize", resize);
    animate();

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener("resize", resize);
      mount.removeChild(renderer.domElement);
      renderer.dispose();
      starGeo.dispose();
      globeGeo.dispose();
      atmoGeo.dispose();
      arcGeo.dispose();
      trailGeo.dispose();
      packetGeo.dispose();
      packet2Geo.dispose();
    };
  }, []);

  return <div className="globe-mount" ref={mountRef} />;
}

// ──────────────────────────────────────────────────────────────────────────────
// Animated counter — ticks up when in view
// ──────────────────────────────────────────────────────────────────────────────
function AnimatedStat({ value, suffix = "", label }: { value: number; suffix?: string; label: string }) {
  const [display, setDisplay] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const triggered = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !triggered.current) {
        triggered.current = true;
        const duration = 1400;
        const start = performance.now();
        const tick = (now: number) => {
          const progress = Math.min((now - start) / duration, 1);
          const eased = 1 - Math.pow(1 - progress, 3);
          setDisplay(Math.round(eased * value));
          if (progress < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }
    }, { threshold: 0.4 });
    observer.observe(el);
    return () => observer.disconnect();
  }, [value]);

  return (
    <div className="wan-stat" ref={ref}>
      <strong>{display}{suffix}</strong>
      <span>{label}</span>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Architecture layer card — animated wave + formula
// ──────────────────────────────────────────────────────────────────────────────
function LayerCard({ layer, index }: { layer: typeof layers[0]; index: number }) {
  const Icon = layer.icon;
  return (
    <motion.article
      className={`layer-card-v2 color-${layer.color}`}
      initial={{ opacity: 0, y: 40 }}
      whileInView={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.08, duration: 0.55 }}
      viewport={{ once: true }}>
      <div className="layer-v2-top">
        <span className="layer-v2-num">{layer.idx}</span>
        <Icon size={20} />
      </div>
      <h3 className="layer-v2-title">{layer.title}</h3>
      <p className="layer-v2-tag">{layer.tag}</p>
      <div className="layer-v2-formula">
        <code>{layer.formula}</code>
      </div>
      <p className="layer-v2-text">{layer.text}</p>
      <div className="layer-v2-metric">
        <span>{layer.metric}</span>
      </div>
    </motion.article>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Performance matrix
// ──────────────────────────────────────────────────────────────────────────────
function PerfRow({ row, index }: { row: typeof perfMatrix[0]; index: number }) {
  const Icon = row.icon;
  return (
    <motion.div
      className={`perf-row ${row.highlight ? "perf-highlight" : ""}`}
      initial={{ opacity: 0, x: -24 }}
      whileInView={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.07 }}
      viewport={{ once: true }}>
      <div className="perf-label">
        <Icon size={16} />
        <span>{row.label}</span>
        <span className="perf-badge">{row.badge}</span>
        <em>{row.scenario}</em>
      </div>
      <div className="perf-bars">
        <div className="perf-bar-row">
          <span className="perf-proto qdap-tag">QDAP</span>
          <div className="perf-bar-wrap qdap-bar">
            <motion.div
              className="perf-fill"
              initial={{ width: 0 }}
              whileInView={{ width: "100%" }}
              transition={{ delay: index * 0.07 + 0.2, duration: 0.7, ease: "easeOut" }}
              viewport={{ once: true }}
            />
          </div>
          <strong className="perf-val">{row.qdap}</strong>
        </div>
        <div className="perf-bar-row">
          <span className="perf-proto">{row.nextLabel}</span>
          <div className="perf-bar-wrap">
            <motion.div
              className="perf-fill secondary"
              initial={{ width: 0 }}
              whileInView={{ width: `${row.nextPct}%` }}
              transition={{ delay: index * 0.07 + 0.35, duration: 0.7, ease: "easeOut" }}
              viewport={{ once: true }}
            />
          </div>
          <strong className="perf-val muted">{row.next}</strong>
        </div>
      </div>
    </motion.div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// IoT heatmap row
// ──────────────────────────────────────────────────────────────────────────────
function IotRow({ row, index }: { row: typeof iotMetrics[0]; index: number }) {
  function cell(val: number, best: boolean) {
    const level = val >= 99 ? "s5" : val >= 96 ? "s4" : val >= 90 ? "s3" : val >= 70 ? "s2" : "s1";
    return (
      <td className={`iot-cell ${level} ${best ? "iot-best" : ""}`}>
        <span>{val.toFixed(0)}%</span>
      </td>
    );
  }
  const vals = [row.qdap, row.mqtt, row.coap, row.amqp];
  const max = Math.max(...vals);
  return (
    <motion.tr
      initial={{ opacity: 0 }}
      whileInView={{ opacity: 1 }}
      transition={{ delay: index * 0.06 }}
      viewport={{ once: true }}>
      <td className="iot-scenario">{row.scenario}</td>
      {cell(row.qdap, row.qdap === max)}
      {cell(row.mqtt, row.mqtt === max)}
      {cell(row.coap, row.coap === max)}
      {cell(row.amqp, row.amqp === max)}
    </motion.tr>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
function MetricBar({ metric }: { metric: ProtocolMetric }) {
  return (
    <div className={`metric-row ${metric.tone}`}>
      <div>
        <strong>{metric.name}</strong>
        <span>{metric.latency}</span>
      </div>
      <div className="bar-stack">
        <div className="bar-line">
          <span>Emergency</span>
          <i style={{ width: `${metric.emergency}%` }} />
          <b>{metric.emergency.toFixed(1)}%</b>
        </div>
        <div className="bar-line total">
          <span>Total</span>
          <i style={{ width: `${metric.total}%` }} />
          <b>{metric.total.toFixed(1)}%</b>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
function App() {
  const [loss, setLoss] = useState(35);
  const [rtt, setRtt] = useState(300);
  const live = useMemo(() => calculateLiveMetrics(loss, rtt), [loss, rtt]);
  const { scrollYProgress } = useScroll();
  const heroShift = useTransform(scrollYProgress, [0, 0.25], [0, -90]);

  return (
    <main>
      <section className="hero">
        <ProtocolField loss={loss} rtt={rtt} />
        <div className="hero-noise" />
        <nav className="nav">
          <div className="brand-mark">
            <span>Q</span>
            <strong>QDAP</strong>
          </div>
          <div className="nav-actions">
            <a href="#architecture">Architecture</a>
            <a href="#performance">Performance</a>
            <a href="#iot">IoT</a>
            <a href="#wan">WAN</a>
            <a href="#install">Install</a>
          </div>
        </nav>

        <motion.div className="hero-content" style={{ y: heroShift }}>
          <div className="hero-copy">
            <p className="eyebrow">Quantum-inspired dynamic application protocol</p>
            <h1>QDAP</h1>
            <p className="hero-lede">
              Crisis-grade delivery, high throughput, low ACK overhead, and Rust-accelerated security
              for networks that stop behaving politely.
            </p>
            <div className="hero-buttons">
              <a className="primary-action" href="#simulation">
                <Zap size={18} />
                Stress the network
              </a>
              <a className="secondary-action" href="#install">
                <Terminal size={18} />
                pip install qdap
              </a>
            </div>
          </div>

          <div className="live-panel" id="simulation">
            <div className="panel-header">
              <span>Live crisis model</span>
              <BadgeCheck size={18} />
            </div>
            <div className="dial-grid">
              <label>
                <span>Loss</span>
                <strong>{loss}%</strong>
                <input type="range" min="0" max="50" value={loss}
                  onChange={(e) => setLoss(Number(e.target.value))} />
              </label>
              <label>
                <span>RTT</span>
                <strong>{rtt}ms</strong>
                <input type="range" min="20" max="500" value={rtt}
                  onChange={(e) => setRtt(Number(e.target.value))} />
              </label>
            </div>
            <div className="live-stats">
              <div><span>QDAP delivery</span><strong>{live.qdapDelivery.toFixed(1)}%</strong></div>
              <div><span>Baseline delivery</span><strong>{live.baselineDelivery.toFixed(1)}%</strong></div>
              <div><span>Throughput edge</span><strong>{live.throughput.toFixed(1)}x</strong></div>
              <div><span>ACK overhead saved</span><strong>{live.ackSaved.toFixed(0)}%</strong></div>
            </div>
          </div>
        </motion.div>
      </section>

      <section className="proof-strip" aria-label="QDAP headline capabilities">
        {features.map((feature) => {
          const Icon = feature.icon;
          return (
            <motion.article className="proof-card" key={feature.label}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}>
              <Icon size={22} />
              <span>{feature.label}</span>
              <strong>{feature.value}</strong>
              <p>{feature.detail}</p>
            </motion.article>
          );
        })}
      </section>

      {/* ── ARCHITECTURE ─────────────────────────────────────────────────────── */}
      <section className="architecture" id="architecture">
        <div className="section-kicker">5-layer protocol stack</div>
        <div className="section-heading">
          <h2>Every layer solves a real network failure mode.</h2>
          <p>
            QFT scheduling, zero-ACK delivery tracking, adaptive FEC, binary delta encoding,
            and parallel streaming — each with concrete math behind it.
          </p>
        </div>
        <div className="layer-grid-v2">
          {layers.map((layer, index) => (
            <LayerCard key={layer.title} layer={layer} index={index} />
          ))}
        </div>
      </section>

      {/* ── PERF MATRIX ──────────────────────────────────────────────────────── */}
      <section className="perf-section" id="performance">
        <div className="perf-wrap">
          <div className="perf-copy">
            <div className="section-kicker">Across all traffic classes</div>
            <h2>Not just emergency.<br />Better across the board.</h2>
            <p>
              Emergency priority is the hardest problem and QDAP solves it completely.
              But the same mechanisms that deliver crisis messages also improve throughput,
              reduce latency, and compress IoT streams in normal conditions.
            </p>
            <div className="perf-legend">
              <div><span className="dot cyan" />QDAP</div>
              <div><span className="dot muted" />Best alternative</div>
            </div>
          </div>
          <div className="perf-rows">
            {perfMatrix.map((row, index) => (
              <PerfRow key={row.label} row={row} index={index} />
            ))}
          </div>
        </div>
      </section>

      {/* ── IOT ──────────────────────────────────────────────────────────────── */}
      <section className="iot-section" id="iot">
        <div className="section-kicker">IoT & sensor networks</div>
        <div className="iot-layout">
          <div className="iot-copy">
            <h2>Emergency delivery across every network condition.</h2>
            <p>
              QDAP's Ghost Session and Delta Encoder were designed for constrained
              devices. Zero ACK overhead means the radio stays quiet. 74.4% frame
              compression means the battery lasts longer.
            </p>
            <div className="iot-highlights">
              <div className="iot-highlight-card">
                <strong>4.2×</strong>
                <span>more messages/sec than MQTT in crisis</span>
              </div>
              <div className="iot-highlight-card">
                <strong>74.4%</strong>
                <span>frame size reduction for sensor streams</span>
              </div>
              <div className="iot-highlight-card">
                <strong>~0</strong>
                <span>ACK packets in hot path</span>
              </div>
            </div>
          </div>
          <div className="iot-table-wrap">
            <table className="iot-table">
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th className="qdap-th">QDAP</th>
                  <th>MQTT 5</th>
                  <th>CoAP</th>
                  <th>AMQP</th>
                </tr>
              </thead>
              <tbody>
                {iotMetrics.map((row, index) => (
                  <IotRow key={row.scenario} row={row} index={index} />
                ))}
              </tbody>
            </table>
            <p className="iot-caption">Emergency message delivery rate · n=300 messages · 10% emergency ratio</p>
          </div>
        </div>
      </section>

      {/* ── CRISIS BENCHMARK ─────────────────────────────────────────────────── */}
      <section className="benchmarks" id="benchmarks">
        <div className="section-kicker">Crisis benchmark · 300ms RTT · 35% loss</div>
        <div className="benchmark-layout">
          <div className="benchmark-copy">
            <h2>300ms RTT. 35% loss. QDAP still lands every emergency.</h2>
            <p>
              The adaptive FEC profile (k=1, r=2) turns every emergency frame into
              3 independent copies. Any single copy reaching the destination is enough.
              Normal traffic still benefits from QFT scheduling and ghost session overhead savings.
            </p>
            <div className="aws-note">
              <Cpu size={20} />
              <span>Validated beyond simulation, including real AWS WAN scenarios.</span>
            </div>
          </div>
          <div className="benchmark-panel">
            {crisisMetrics.map((metric) => (
              <MetricBar key={metric.name} metric={metric} />
            ))}
          </div>
        </div>
      </section>

      {/* ── WAN GLOBE ────────────────────────────────────────────────────────── */}
      <section className="globe-section" id="wan">
        <div className="globe-copy">
          <div className="section-kicker">AWS WAN · eu-west-1 → ap-southeast-1</div>
          <h2>Ireland to Singapore.<br />96× faster.</h2>
          <p>
            Real AWS WAN test under 300ms RTT and injected packet loss.
            QDAP keeps the pipeline saturated while competing protocols stall waiting for acknowledgment rounds.
          </p>
          <div className="wan-stats">
            <AnimatedStat value={96} suffix="×" label="throughput" />
            <AnimatedStat value={65} suffix="×" label="p99 lower latency" />
            <AnimatedStat value={100} suffix="%" label="emergency delivery" />
          </div>
          <p className="globe-footnote">
            tc netem loss injection · real EC2 instances · QDAP vs HTTP/3 QUIC
          </p>
        </div>
        <motion.div
          className="globe-wrap"
          initial={{ opacity: 0, scale: 0.92 }}
          whileInView={{ opacity: 1, scale: 1 }}
          transition={{ duration: 1.1, ease: "easeOut" }}
          viewport={{ once: true, margin: "-120px" }}>
          <WANGlobe />
          <div className="globe-label globe-label-ireland">
            <div className="globe-dot cyan" />
            <span>Dublin</span>
          </div>
          <div className="globe-label globe-label-singapore">
            <div className="globe-dot amber" />
            <span>Singapore</span>
          </div>
        </motion.div>
      </section>

      <section className="security-band">
        <div className="security-copy">
          <LockKeyhole size={30} />
          <h2>Security that stays in the data path.</h2>
          <p>
            X25519 handshake, AES-256-GCM frame protection, key rotation, session tickets, and Rust
            core acceleration make the protocol practical without turning security into a throughput tax.
          </p>
        </div>
        <div className="security-grid">
          <span>X25519 ECDH</span>
          <span>AES-256-GCM</span>
          <span>0-RTT resumption</span>
          <span>Rust fallback safe</span>
        </div>
      </section>

      <section className="install" id="install">
        <div className="section-kicker">Developer ready</div>
        <div className="install-layout">
          <div>
            <h2>Use Python. Accelerate with Rust. Keep the same architecture.</h2>
            <p>
              QDAP is designed to be installable, testable, and reproducible: pure Python fallback
              when Rust is absent, compiled hot paths when the core is available.
            </p>
            <div className="install-actions">
              <a href="https://github.com/qdap-protocol/qdap">
                <Github size={18} />
                GitHub
              </a>
              <a href="#simulation">
                <SignalHigh size={18} />
                Try the model
              </a>
            </div>
          </div>
          <pre className="code-panel"><code>{`pip install qdap

from qdap import AdaptiveFEC, QFTScheduler

scheduler = QFTScheduler()
chunk = scheduler.decide(
    payload_size=64 * 1024,
    rtt_ms=300,
    loss_rate=0.35
)

fec = AdaptiveFEC()
packets, profile = fec.encode(
    b"emergency telemetry",
    is_emergency=True
)

print(chunk, profile.label, len(packets))`}</code></pre>
        </div>
      </section>

      <footer>
        <div className="brand-mark">
          <span>Q</span>
          <strong>QDAP</strong>
        </div>
        <p>Quantum-inspired protocol engineering for unreliable networks.</p>
        <a href="#top">
          Back to top
          <ArrowRight size={16} />
        </a>
      </footer>
    </main>
  );
}

export default App;
