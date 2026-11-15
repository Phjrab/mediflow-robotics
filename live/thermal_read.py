"""Read-only temperature sampling through a strict individual reply guard."""
import time


def read_temperatures(bus, ids, bulk, strict_factory, *, traces=None, stop_c=55, emergency_c=70):
    """Confirm a hot reading on that axis before returning the protection input.

    A hot bulk or individual read gets two immediate individual confirmations.
    Either hot confirmation retains HOLD; two cool confirmations mark a spike.
    At >=70C a second >=70C response ends confirmation after that first reread.
    Invalid/failed replies always raise, including during confirmation.
    """
    if not 0 < stop_c < emergency_c <= 255:
        raise ValueError('온도 재확인 기준 오류')
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
            values, reply_traces = [], []

            def take():
                value, trace = read(joint, mid)
                values.append(value)
                reply_traces.append(trace)
                return value

            trigger = bulk[joint] if bulk[joint] >= stop_c else None
            trigger_source = 'bulk' if trigger is not None else None
            if trigger is None:
                value = take()
                if value >= stop_c:
                    trigger, trigger_source = value, 'individual'
                elif value != bulk[joint]:
                    second = take()
                    value = max(value, second)
                    if second >= stop_c:
                        trigger, trigger_source = second, 'individual_disagreement'
            confirmation = None
            if trigger is not None:
                rereads = [take()]
                emergency = trigger >= emergency_c and rereads[0] >= emergency_c
                if not emergency:
                    rereads.append(take())
                hot = [v for v in rereads if v >= stop_c]
                value = max([trigger, *hot]) if hot else max(rereads)
                confirmation = dict(trigger_c=trigger, trigger_source=trigger_source,
                                    rereads_c=rereads, status='confirmed' if hot else 'spike',
                                    emergency_fast_stop=emergency)
            if trigger is not None or any(v != bulk[joint] for v in values):
                disagreements[joint] = {'bulk_c': bulk[joint], 'individual_reads_c': values,
                                        'adopted_c': value, 'reply_trace': reply_traces}
                if confirmation is not None:
                    disagreements[joint]['confirmation'] = confirmation
            temperatures[joint] = value
    return temperatures, disagreements
