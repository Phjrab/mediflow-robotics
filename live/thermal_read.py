"""Read-only temperature sampling through a strict individual reply guard."""
import time


def read_temperatures(bus, ids, bulk, strict_factory, *, traces=None, last_requests=None,
                      minimum_interval_s=.020, clock=time.monotonic, pause=time.sleep):
    """One new individual READ per axis, paced >=20ms from its previous READ.

    No sync-read cache is used for protection and no bulk/individual maximum is
    substituted for the individual value. ThermalWindow owns the time decision.
    Invalid replies raise immediately. The caller persists last_requests.
    """
    if minimum_interval_s < .020:
        raise ValueError('개별 온도 읽기 간격은 20ms 이상')
    last_requests = {} if last_requests is None else last_requests
    temperatures, disagreements = {}, {}
    with strict_factory(bus.packet_handler) as strict:
        def read(joint, mid):
            previous = last_requests.get(joint)
            if previous is not None:
                pause(max(0., previous+minimum_interval_s-clock()))
            requested = clock()
            if previous is not None and requested-previous < minimum_interval_s-1e-9:
                raise RuntimeError('온도 개별 READ 간격 미준수')
            last_requests[joint] = requested
            queued = None
            try:
                queued = bus.port_handler.getBytesAvailable()
            except Exception:
                # Optional observation only; never flush or consume input here.
                pass
            try:
                data, communication, status = bus.packet_handler.readTxRx(bus.port_handler, mid, 63, 1)
            except BaseException as error:
                if traces is not None:
                    traces.append({'joint': joint, 'error': str(error),
                                   'observed': getattr(error, 'observed', {}),
                                   'queued_before_read': queued})
                raise
            if (type(communication) is not int or communication != 0
                    or type(status) is not int or status != 0
                    or not isinstance(data, (bytes, bytearray, list, tuple)) or len(data) != 1
                    or type(data[0]) is not int or not 0 <= data[0] <= 255):
                raise RuntimeError('Individual temperature decode/communication/status mismatch')
            row = strict.history[-1]
            # Exclude changing wall time to preserve the caller's changed-only logging.
            trace = {key: row[key] for key in (
                'address', 'expected_id', 'expected_bytes', 'raw_hex', 'communication', 'status')}
            trace['queued_before_read'] = queued
            raw = bytes.fromhex(trace['raw_hex'])
            request = bytes((255,255,mid,4,2,63,1))
            trace.update(received_id=raw[2], checksum_valid=sum(raw[2:]) % 256 == 255,
                         tx_hex=(request+bytes(((~sum(request[2:])) & 255,))).hex(),
                         request_started_monotonic_s=requested,
                         request_interval_s=None if previous is None else requested-previous,
                         observed_monotonic_s=clock())
            if traces is not None:
                traces.append({'joint': joint, **trace, 'value_c': data[0]})
            return data[0], trace
        for joint, mid in ids.items():
            value, trace = read(joint, mid)
            if value != bulk[joint] or value >= 55:
                disagreements[joint] = {'bulk_c': bulk[joint], 'individual_reads_c': [value],
                                        'adopted_c': value, 'reply_trace': [trace]}
            temperatures[joint] = value
    return temperatures, disagreements
