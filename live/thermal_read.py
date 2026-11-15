"""Read-only temperature sampling through a strict individual reply guard."""
import time


def read_temperatures(bus, ids, bulk, strict_factory, *, traces=None):
    """Return individual temperatures; repeat disagreements and retain the hotter read."""
    temperatures, disagreements = {}, {}
    with strict_factory(bus.packet_handler) as strict:
        def read(joint, mid):
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
            if traces is not None:
                traces.append({'joint': joint, **trace, 'value_c': data[0],
                               'observed_monotonic_s': time.monotonic()})
            return data[0], trace
        for joint, mid in ids.items():
            first, trace = read(joint, mid)
            values, reply_traces = [first], [trace]
            value = first
            if first != bulk[joint]:
                second, trace = read(joint, mid)
                values.append(second)
                reply_traces.append(trace)
                value = max(first, second)
            if first != bulk[joint] or first >= 55:
                disagreements[joint] = {'bulk_c': bulk[joint], 'individual_reads_c': values,
                                        'adopted_c': value, 'reply_trace': reply_traces}
            temperatures[joint] = value
    return temperatures, disagreements
