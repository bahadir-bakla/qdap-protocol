-- QDAP Wireshark Dissector
-- ========================
--
-- Wireshark Lua dissector for QDAP protocol frames.
--
-- Installation:
--   Copy this file to your Wireshark plugins directory:
--     macOS:  ~/.local/lib/wireshark/plugins/
--     Linux:  ~/.local/lib/wireshark/plugins/
--     Win:    %APPDATA%\Wireshark\plugins\
--
-- Usage:
--   QDAP runs over TCP. The dissector registers on the QDAP magic bytes.
--   If needed, manually decode a TCP stream as QDAP via:
--     Decode As → QDAP

local qdap_proto = Proto("qdap", "Quantum-Inspired Dynamic Application Protocol")

-- ─── Transport Header Fields ────────────────────────────
local f_magic     = ProtoField.bytes("qdap.magic",      "Magic",          base.SPACE)
local f_tversion  = ProtoField.uint16("qdap.tversion",  "Transport Version", base.DEC)
local f_length    = ProtoField.uint32("qdap.length",    "Payload Length",  base.DEC)

-- ─── QFrame Header Fields ───────────────────────────────
local f_version   = ProtoField.uint8("qdap.version",     "QDAP Version",    base.DEC)
local f_frametype = ProtoField.uint8("qdap.frametype",   "Frame Type",      base.HEX)
local f_sfcount   = ProtoField.uint16("qdap.sfcount",    "Subframe Count",  base.DEC)
local f_flags     = ProtoField.uint16("qdap.flags",      "Flags",           base.HEX)
local f_sessionid = ProtoField.uint64("qdap.sessionid",  "Session ID",      base.HEX)

-- ─── Amplitude Vector ───────────────────────────────────
local f_amplitude = ProtoField.float("qdap.amplitude",   "Amplitude")

-- ─── Subframe Fields ────────────────────────────────────
local f_sf_len    = ProtoField.uint32("qdap.sf.length",  "Payload Length",  base.DEC)
local f_sf_type   = ProtoField.uint8("qdap.sf.type",     "Subframe Type",   base.HEX)
local f_sf_seq    = ProtoField.uint32("qdap.sf.seqnum",  "Sequence Number", base.DEC)
local f_sf_data   = ProtoField.bytes("qdap.sf.payload",  "Payload",         base.SPACE)

-- ─── Integrity ──────────────────────────────────────────
local f_hash      = ProtoField.bytes("qdap.hash",        "Integrity Hash (SHA3-256)", base.SPACE)

qdap_proto.fields = {
    f_magic, f_tversion, f_length,
    f_version, f_frametype, f_sfcount, f_flags, f_sessionid,
    f_amplitude,
    f_sf_len, f_sf_type, f_sf_seq, f_sf_data,
    f_hash,
}

-- Frame type names
local frame_type_names = {
    [0x01] = "DATA",
    [0x02] = "CTRL",
    [0x03] = "GHOST",
    [0x04] = "PROBE",
    [0x05] = "SYNC",
}

local subframe_type_names = {
    [0x01] = "DATA",
    [0x02] = "CTRL",
    [0x03] = "GHOST",
    [0x04] = "PROBE",
    [0x05] = "SYNC",
}

function qdap_proto.dissector(buffer, pinfo, tree)
    -- Check minimum length for transport header (10 bytes)
    if buffer:len() < 10 then return end

    -- Verify magic bytes "QDAP" (0x51 0x44 0x41 0x50)
    local magic = buffer(0, 4):bytes()
    if magic:raw() ~= "QDAP" then return end

    pinfo.cols.protocol = "QDAP"

    local subtree = tree:add(qdap_proto, buffer(), "QDAP Protocol")
    local offset = 0

    -- ─── Transport Header ───────────────────────────────
    local transport = subtree:add(qdap_proto, buffer(0, 10), "Transport Header")
    transport:add(f_magic, buffer(0, 4))
    transport:add(f_tversion, buffer(4, 2))
    local payload_len = buffer(6, 4):uint()
    transport:add(f_length, buffer(6, 4))
    offset = 10

    -- ─── QFrame Header ──────────────────────────────────
    if buffer:len() < offset + 6 then return end

    local qframe = subtree:add(qdap_proto, buffer(offset, 6), "QFrame Header")
    qframe:add(f_version, buffer(offset, 1))
    offset = offset + 1

    local ft = buffer(offset, 1):uint()
    local ft_item = qframe:add(f_frametype, buffer(offset, 1))
    ft_item:append_text(" (" .. (frame_type_names[ft] or "UNKNOWN") .. ")")
    offset = offset + 1

    local sf_count = buffer(offset, 2):uint()
    qframe:add(f_sfcount, buffer(offset, 2))
    offset = offset + 2

    qframe:add(f_flags, buffer(offset, 2))
    offset = offset + 2

    -- Session ID (8 bytes)
    qframe:add(f_sessionid, buffer(offset, 8))
    offset = offset + 8

    -- ─── Amplitude Vector ───────────────────────────────
    if sf_count > 0 then
        local amp_len = sf_count * 4
        local amp_tree = subtree:add(qdap_proto, buffer(offset, amp_len), "Amplitude Vector")
        for i = 0, sf_count - 1 do
            local val = buffer(offset + i * 4, 4):float()
            amp_tree:add(f_amplitude, buffer(offset + i * 4, 4)):set_text(
                string.format("α[%d] = %.6f  (|α|² = %.6f)", i, val, val * val)
            )
        end
        offset = offset + amp_len
    end

    -- ─── Subframes ──────────────────────────────────────
    for i = 0, sf_count - 1 do
        if buffer:len() < offset + 9 then return end

        local sf_payload_len = buffer(offset, 4):uint()
        local sf_type = buffer(offset + 4, 1):uint()
        local sf_seq = buffer(offset + 5, 4):uint()
        local sf_total = 9 + sf_payload_len

        local sf_tree = subtree:add(
            qdap_proto,
            buffer(offset, sf_total),
            string.format("Subframe #%d [%s] seq=%d len=%d",
                i, subframe_type_names[sf_type] or "?", sf_seq, sf_payload_len)
        )
        sf_tree:add(f_sf_len, buffer(offset, 4))
        sf_tree:add(f_sf_type, buffer(offset + 4, 1))
        sf_tree:add(f_sf_seq, buffer(offset + 5, 4))
        if sf_payload_len > 0 then
            sf_tree:add(f_sf_data, buffer(offset + 9, sf_payload_len))
        end

        offset = offset + sf_total
    end

    -- ─── Integrity Hash ─────────────────────────────────
    if buffer:len() >= offset + 32 then
        subtree:add(f_hash, buffer(offset, 32))
        offset = offset + 32
    end

    pinfo.cols.info = string.format(
        "QDAP %s session=0x%x subframes=%d",
        frame_type_names[ft] or "?", buffer(14, 8):uint64():tonumber(), sf_count
    )
end

-- Register on TCP (no fixed port — use "Decode As" in Wireshark)
local tcp_table = DissectorTable.get("tcp.port")
tcp_table:add(9000, qdap_proto)  -- Default QDAP port
