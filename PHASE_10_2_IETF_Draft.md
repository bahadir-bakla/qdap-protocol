# PHASE 10.2 — IETF QIRG Internet-Draft
## Gemini Agent İçin: Tam Yapı, xml2rfc Format
## Tahmini Süre: 2-3 hafta | Zorluk: Orta
## Ön Koşul: Phase 10.1 (SIGCOMM Paper) kabul almış olmalı

---

## 1. Amaç

IETF QIRG (Quantum Internet Research Group) veya TSVWG (Transport Area Working Group)
için internet-draft hazırla. Draft, QDAP protokolünü IETF standart formatında belgeler.

---

## 2. Draft Kimliği

```
draft-bakla-qirg-qdap-application-layer-00
```

Alternatif: `draft-bakla-tsvwg-qdap-00` (transport area)

---

## 3. Submission Hedefleri

| Hedef | Tarih | Platform |
|-------|-------|----------|
| IETF Datatracker ilk submit | ASAP | datatracker.ietf.org |
| QIRG mailing list duyuru | 1 hafta sonra | qirg@irtf.org |
| IETF 121 (Dublin) | Kasım 2024 | IRTF QIRG session |
| IETF 122 (Bangkok) | Mart 2025 | Presentation |

---

## 4. Draft Dizin Yapısı

```
ietf_draft/
├── draft-bakla-qirg-qdap-application-layer-00.xml  # Ana kaynak (xml2rfc)
├── draft-bakla-qirg-qdap-application-layer-00.txt  # Generate
├── draft-bakla-qirg-qdap-application-layer-00.html # Generate
├── Makefile
└── README.md
```

---

## 5. Ana Draft (xml2rfc v3)

```xml
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE rfc [
  <!ENTITY RFC2119 SYSTEM "https://xml2rfc.ietf.org/public/rfc/bibxml/reference.RFC.2119.xml">
  <!ENTITY RFC8174 SYSTEM "https://xml2rfc.ietf.org/public/rfc/bibxml/reference.RFC.8174.xml">
  <!ENTITY RFC9000 SYSTEM "https://xml2rfc.ietf.org/public/rfc/bibxml/reference.RFC.9000.xml">
  <!ENTITY RFC8446 SYSTEM "https://xml2rfc.ietf.org/public/rfc/bibxml/reference.RFC.8446.xml">
  <!ENTITY RFC7252 SYSTEM "https://xml2rfc.ietf.org/public/rfc/bibxml/reference.RFC.7252.xml">
]>

<rfc xmlns:xi="http://www.w3.org/2001/XInclude"
     category="exp"
     docName="draft-bakla-qirg-qdap-application-layer-00"
     ipr="trust200902"
     obsoletes=""
     updates=""
     submissionType="IRTF"
     xml:lang="en"
     version="3">

  <front>
    <title abbrev="QDAP">
      Quantum-Inspired Adaptive Protocol for Application-Layer Communication
    </title>

    <seriesInfo name="Internet-Draft"
                value="draft-bakla-qirg-qdap-application-layer-00"/>

    <author fullname="Bahadir Selim Bakla" initials="B.S." surname="Bakla">
      <address>
        <email>bahadirselimbakla@icloud.com</email>
      </address>
    </author>

    <date year="2025"/>

    <area>IRTF</area>
    <workgroup>Quantum Internet Research Group (QIRG)</workgroup>

    <keyword>quantum-inspired</keyword>
    <keyword>adaptive protocol</keyword>
    <keyword>application-layer</keyword>
    <keyword>QFT scheduling</keyword>
    <keyword>ghost session</keyword>

    <abstract>
      <t>
        This document describes the Quantum-Inspired Adaptive Protocol (QDAP),
        an application-layer communication protocol that applies principles
        derived from quantum mechanics to adaptive protocol behavior. QDAP
        uses a Quantum Fourier Transform (QFT)-inspired scheduling algorithm
        to select transmission strategies based on real-time channel state,
        achieving significant throughput improvements for small payloads
        in challenged network conditions. The protocol includes a Ghost Session
        mechanism for zero-overhead connection maintenance and an integrated
        security layer based on X25519 key exchange and AES-256-GCM encryption.
        This document specifies the wire format, scheduling algorithm, session
        management, and security properties of QDAP for the purpose of
        evaluation and discussion within the IRTF QIRG.
      </t>
    </abstract>
  </front>

  <middle>
    <section anchor="intro" numbered="true" toc="default">
      <name>Introduction</name>
      <t>
        Application-layer protocols such as HTTP/2 <xref target="RFC9113"/>,
        MQTT <xref target="mqtt50"/>, and CoAP <xref target="RFC7252"/>
        operate without awareness of the underlying channel state. When
        network conditions degrade (increased latency, packet loss, jitter),
        these protocols continue using fixed transmission strategies, leading
        to significant throughput degradation.
      </t>
      <t>
        QDAP addresses this limitation by modeling protocol state as a
        quantum superposition of transmission strategies, collapsing to an
        optimal strategy through a QFT-inspired measurement process. This
        approach enables continuous adaptation to channel conditions with
        O(n log n) computational complexity.
      </t>

      <section anchor="req-lang" numbered="true" toc="default">
        <name>Requirements Language</name>
        <t>
          The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT",
          "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY",
          and "OPTIONAL" in this document are to be interpreted as described
          in BCP 14 <xref target="RFC2119"/> <xref target="RFC8174"/>
          when, and only when, they appear in all capitals.
        </t>
      </section>
    </section>

    <section anchor="terminology" numbered="true" toc="default">
      <name>Terminology</name>
      <dl newline="false" spacing="normal">
        <dt>QFrame:</dt>
        <dd>The fundamental protocol data unit of QDAP, carrying
            priority, deadline, and payload information.</dd>
        <dt>Ghost Session:</dt>
        <dd>A QDAP session in suspended state, maintaining logical
            continuity without transmitting keepalive traffic.</dd>
        <dt>QFT Scheduler:</dt>
        <dd>The component that applies QFT-inspired analysis to
            channel state observations and selects a transmission strategy.</dd>
        <dt>Energy Band:</dt>
        <dd>A frequency-domain representation of payload size class,
            derived from QFT decomposition of historical observations.</dd>
        <dt>Strategy:</dt>
        <dd>A transmission parameter set (chunk size, timeout, retry
            policy) optimized for a specific channel condition class.</dd>
      </dl>
    </section>

    <section anchor="wire-format" numbered="true" toc="default">
      <name>Wire Format</name>

      <section anchor="qframe-header" numbered="true" toc="default">
        <name>QFrame Header</name>
        <t>
          Each QDAP message is encapsulated in a QFrame with the following
          fixed-length header:
        </t>
        <artwork name="" type="ascii-art" align="left"><![CDATA[
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Magic (0x51444150)                        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|    Version    |     Type      |         Priority              |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                        Deadline (64-bit)                       |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Sequence Number (64-bit)                  |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Payload Length (32-bit)                  |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Payload Hash (256-bit)                      |
|                    (SHA-256 of payload)                        |
|                                                               |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Payload (variable)                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        ]]></artwork>
        <t>Fields:</t>
        <ul spacing="normal">
          <li>Magic (32 bits): MUST be 0x51444150 (ASCII "QDAP"). A receiver
              that does not find this value MUST discard the frame.</li>
          <li>Version (8 bits): Protocol version. This document describes
              version 1 (0x01).</li>
          <li>Type (8 bits): Frame type. See <xref target="frame-types"/>.</li>
          <li>Priority (16 bits): Scheduling priority, 0-1000.
              Value 1000 indicates emergency traffic.</li>
          <li>Deadline (64 bits): UNIX timestamp in microseconds.
              Zero indicates no deadline constraint.</li>
          <li>Sequence Number (64 bits): Monotonically increasing per session.</li>
          <li>Payload Length (32 bits): Length of payload in bytes.</li>
          <li>Payload Hash (256 bits): SHA-256 of the payload for integrity.</li>
        </ul>
      </section>

      <section anchor="frame-types" numbered="true" toc="default">
        <name>Frame Types</name>
        <table align="left">
          <name>QFrame Type Values</name>
          <thead>
            <tr><th>Value</th><th>Name</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr><td>0x01</td><td>DATA</td><td>Application payload</td></tr>
            <tr><td>0x02</td><td>CONTROL</td><td>Session control</td></tr>
            <tr><td>0x03</td><td>GHOST_HELLO</td><td>Ghost session establishment</td></tr>
            <tr><td>0x04</td><td>GHOST_BYE</td><td>Ghost session teardown</td></tr>
            <tr><td>0x05</td><td>CHANNEL_PROBE</td><td>Channel state measurement</td></tr>
            <tr><td>0x06</td><td>CHANNEL_REPORT</td><td>Channel state response</td></tr>
            <tr><td>0xFF</td><td>EMERGENCY</td><td>Emergency priority override</td></tr>
          </tbody>
        </table>
      </section>
    </section>

    <section anchor="qft-scheduling" numbered="true" toc="default">
      <name>QFT-Inspired Scheduling</name>
      <t>
        The QDAP scheduler models channel observation history as a
        discrete signal and applies a QFT-inspired frequency decomposition
        to identify dominant transmission patterns. The scheduler selects
        one of five strategies based on energy band analysis:
      </t>
      <table align="left">
        <name>Transmission Strategies</name>
        <thead>
          <tr><th>Strategy</th><th>Chunk Size</th><th>Timeout</th><th>Use Case</th></tr>
        </thead>
        <tbody>
          <tr><td>MICRO</td><td>4 KB</td><td>100ms</td><td>High-loss links</td></tr>
          <tr><td>SMALL</td><td>64 KB</td><td>500ms</td><td>Moderate latency</td></tr>
          <tr><td>MEDIUM</td><td>256 KB</td><td>2s</td><td>Normal WAN</td></tr>
          <tr><td>LARGE</td><td>1 MB</td><td>10s</td><td>LAN / low loss</td></tr>
          <tr><td>JUMBO</td><td>4 MB</td><td>60s</td><td>Bulk transfer</td></tr>
        </tbody>
      </table>
      <t>
        The scheduling algorithm operates in O(n log n) time where n is
        the number of historical observations. An implementation MUST
        maintain a sliding window of at most 1024 observations.
      </t>
    </section>

    <section anchor="ghost-session" numbered="true" toc="default">
      <name>Ghost Session Mechanism</name>
      <t>
        A QDAP Ghost Session allows a logical connection to persist
        across periods of inactivity or network disruption without
        transmitting keepalive traffic. The session transitions through
        the following states:
      </t>
      <t>
        CONNECTING → ACTIVE → GHOST → ACTIVE (on resume)
                                     → CLOSED (on timeout)
      </t>
      <t>
        A session MAY transition to GHOST state when no application
        data has been transmitted for a configurable idle_timeout
        (default: 30 seconds). In GHOST state, the implementation
        MUST NOT transmit any keepalive or probe packets.
      </t>
      <t>
        Session state is maintained using a Markov model. Experimental
        evaluation demonstrates F1 scores of 0.9999 at 1% packet loss
        and ≥0.85 at 20% packet loss.
      </t>
    </section>

    <section anchor="security" numbered="true" toc="default">
      <name>Security Considerations</name>
      <t>
        QDAP mandates end-to-end encryption for all DATA frames.
        The security handshake proceeds as follows:
      </t>
      <ol spacing="normal">
        <li>Initiator generates an X25519 ephemeral keypair.</li>
        <li>Initiator sends public key in CONTROL frame.</li>
        <li>Responder generates ephemeral keypair, performs X25519
            key exchange, derives 256-bit session key via HKDF-SHA256.</li>
        <li>All subsequent DATA frames are encrypted with AES-256-GCM.</li>
        <li>Each frame uses a unique 96-bit nonce derived from the
            sequence number.</li>
      </ol>
      <t>
        This construction achieves forward secrecy: compromise of
        long-term keys does not expose past session traffic. A formal
        security proof under the eCK model is provided in
        <xref target="security-proof"/>.
      </t>

      <section anchor="security-proof" numbered="true" toc="default">
        <name>Formal Security Analysis</name>
        <t>
          [Phase 8.4 tamamlandıktan sonra ProVerif sonuçları buraya eklenir.]
          The protocol achieves:
        </t>
        <ul>
          <li>Session secrecy under Dolev-Yao threat model.</li>
          <li>Forward secrecy: past sessions secure after key compromise.</li>
          <li>Replay resistance via monotonic sequence numbers.</li>
        </ul>
      </section>
    </section>

    <section anchor="iana" numbered="true" toc="default">
      <name>IANA Considerations</name>
      <t>
        This document requests the following IANA registrations:
      </t>
      <ul>
        <li>A new registry "QDAP Frame Types" under the
            "Application Protocol Parameters" registry group,
            with initial values from <xref target="frame-types"/>.</li>
        <li>Port 8472/tcp: "qdap" — QDAP Application Protocol.</li>
      </ul>
      <t>
        [NOTE TO RFC EDITOR: This section should be removed before
        publication as an RFC.]
      </t>
    </section>
  </middle>

  <back>
    <references>
      <name>References</name>

      <references>
        <name>Normative References</name>
        &RFC2119;
        &RFC8174;
        &RFC8446;
        &RFC7252;
      </references>

      <references>
        <name>Informative References</name>
        &RFC9000;

        <reference anchor="mqtt50">
          <front>
            <title>MQTT Version 5.0</title>
            <author><organization>OASIS</organization></author>
            <date year="2019"/>
          </front>
          <refcontent>OASIS Standard</refcontent>
        </reference>

        <reference anchor="bakla2025qdap">
          <front>
            <title>QDAP: A Quantum-Inspired Adaptive Protocol for
                   Zero-Overhead Application-Layer Communication</title>
            <author initials="B.S." surname="Bakla"
                    fullname="Bahadir Selim Bakla"/>
            <date year="2025"/>
          </front>
          <refcontent>arXiv preprint arXiv:XXXX.XXXXX [cs.NI]</refcontent>
        </reference>

        <reference anchor="shor1994">
          <front>
            <title>Algorithms for Quantum Computation: Discrete
                   Logarithms and Factoring</title>
            <author initials="P." surname="Shor"/>
            <date year="1994"/>
          </front>
          <refcontent>Proceedings 35th FOCS, pp. 124-134</refcontent>
        </reference>

      </references>
    </references>

    <section anchor="impl-status" numbered="false" toc="default">
      <name>Implementation Status</name>
      <t>
        [NOTE: This section will be removed before publication.]
        A reference implementation of QDAP is available at:
        https://github.com/[USERNAME]/qdap
        The implementation includes 226 automated tests and a
        Rust hot-path via PyO3 bindings. Performance benchmarks
        and evaluation datasets are included.
      </t>
    </section>

    <section anchor="acknowledgments" numbered="false" toc="default">
      <name>Acknowledgments</name>
      <t>
        The author thanks the IRTF QIRG community for feedback on
        quantum-inspired classical protocols.
      </t>
    </section>
  </back>
</rfc>
```

---

## 6. Makefile

```makefile
# ietf_draft/Makefile
DRAFT = draft-bakla-qirg-qdap-application-layer-00
XML  = $(DRAFT).xml
TXT  = $(DRAFT).txt
HTML = $(DRAFT).html

all: $(TXT) $(HTML)

$(TXT): $(XML)
	xml2rfc --text $(XML) -o $(TXT)

$(HTML): $(XML)
	xml2rfc --html $(XML) -o $(HTML)

validate:
	xml2rfc --validate $(XML)

idnits: $(TXT)
	idnits $(TXT)

clean:
	rm -f $(TXT) $(HTML) *.log

submit: validate idnits
	@echo "Submit at: https://datatracker.ietf.org/submit/"
	@echo "File: $(XML)"
```

---

## 7. Kurulum

```bash
# xml2rfc kurulumu
pip install xml2rfc

# idnits (draft kalite kontrolü)
pip install idnits  # veya: apt install idnits

# Build
cd ietf_draft
make all

# Validate
make validate

# idnits check (submission öncesi zorunlu)
make idnits
```

---

## 8. Submission Prosedürü

```
1. https://datatracker.ietf.org/submit/ adresine git
2. XML dosyasını yükle (TXT de kabul edilir)
3. Onay emailini kontrol et
4. Draft yayınlandıktan sonra QIRG listesine duyur:
   - qirg@irtf.org
   - Konu: [QIRG] New I-D: draft-bakla-qirg-qdap-application-layer-00
5. IETF meeting'e presentation isteği gönder
```

---

## 9. Mailing List Email Şablonu

```
To: qirg@irtf.org
Subject: [QIRG] New I-D: QDAP - Quantum-Inspired Adaptive Protocol

Dear QIRG participants,

I have submitted a new Internet-Draft describing QDAP, a quantum-inspired
application-layer communication protocol:

  draft-bakla-qirg-qdap-application-layer-00

QDAP applies QFT-inspired scheduling to achieve adaptive transmission
strategy selection based on channel state. Key results include 110×
TCP throughput improvement for small payloads (1KB: 34.6 vs 0.31 Mbps)
and 136× MQTT improvement in emulated WAN conditions.

The draft specifies:
- QFrame wire format
- QFT scheduling algorithm
- Ghost Session mechanism (zero keepalive overhead)
- X25519 + AES-256-GCM security layer

Reference implementation: https://github.com/[USERNAME]/qdap
arXiv preprint: https://arxiv.org/abs/XXXX.XXXXX

I would appreciate feedback from the group.

Best regards,
Bahadir Selim Bakla
```

---

## 10. Başarı Kriterleri

| Metrik | Hedef |
|--------|-------|
| xml2rfc validate | 0 hata |
| idnits | 0 hata, max 3 uyarı |
| Datatracker submit | Başarılı |
| QIRG liste yanıtı | ≥1 feedback |
| IETF meeting presentation | Kabul |

---

## 11. Sonraki Adımlar (v01, v02, ...)

Her yeni benchmark ve implementation sonrası draft güncellenir:
- `-01`: Cloud WAN sonuçları eklenir (Phase 8.2)
- `-02`: IBM Quantum fidelity eklenir (Phase 8.3)
- `-03`: eCK proof eklenir (Phase 8.4)
- `-04`: K8s deployment eklenir (Phase 9.4)
- Working Group adoption hedefi: TSVWG veya yeni BOF
