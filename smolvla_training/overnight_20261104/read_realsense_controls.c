/* RealSense 카메라의 V4L2 설정 조회만 허용한다. 설정·영상·모터 쓰기 없음. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/videodev2.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static const char *device = "/dev/v4l/by-id/usb-Intel_R__RealSense_TM__Depth_Camera_435_Intel_R__RealSense_TM__Depth_Camera_435-video-index0";

static void quoted(const unsigned char *s, size_t limit) {
    putchar('"');
    for (size_t i = 0; i < limit && s[i]; ++i) {
        unsigned char ch = s[i];
        if (ch == '"' || ch == '\\') printf("\\%c", ch);
        else if (ch < 32 || ch > 126) printf("\\u%04x", (unsigned int)ch);
        else putchar(ch);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    if (argc != 2 || strcmp(argv[1], device)) {
        fputs("RealSense 검증 경로만 허용\n", stderr);
        return 2;
    }
    int fd = open(device, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) { perror("camera_readonly_open"); return 3; }
    struct v4l2_capability capability = {0};
    if (ioctl(fd, VIDIOC_QUERYCAP, &capability)) {
        perror("VIDIOC_QUERYCAP"); close(fd); return 4;
    }
    printf("{\"driver\":"); quoted(capability.driver, sizeof capability.driver);
    printf(",\"card\":"); quoted(capability.card, sizeof capability.card);
    struct v4l2_format format = {0}; format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (!ioctl(fd, VIDIOC_G_FMT, &format)) {
        uint32_t pix = format.fmt.pix.pixelformat;
        unsigned char fourcc[5] = {pix & 255, (pix >> 8) & 255, (pix >> 16) & 255, (pix >> 24) & 255, 0};
        printf(",\"format\":{\"width\":%u,\"height\":%u,\"fourcc\":", format.fmt.pix.width, format.fmt.pix.height);
        quoted(fourcc, 4);
        printf(",\"colorspace\":%u,\"ycbcr_encoding\":%u,\"quantization\":%u,\"transfer\":%u}",
               format.fmt.pix.colorspace, format.fmt.pix.ycbcr_enc, format.fmt.pix.quantization, format.fmt.pix.xfer_func);
    }
    struct v4l2_streamparm parm = {0}; parm.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (!ioctl(fd, VIDIOC_G_PARM, &parm)) {
        printf(",\"frame_interval\":{\"numerator\":%u,\"denominator\":%u}",
               parm.parm.capture.timeperframe.numerator, parm.parm.capture.timeperframe.denominator);
    }
    printf(",\"controls\":[");
    struct v4l2_queryctrl query = {0}; query.id = V4L2_CTRL_FLAG_NEXT_CTRL;
    unsigned int count = 0;
    while (!ioctl(fd, VIDIOC_QUERYCTRL, &query)) {
        if (++count > 128) { fputs("control enumeration limit\n", stderr); close(fd); return 5; }
        if (count > 1) putchar(',');
        printf("{\"id\":%u,\"name\":", query.id); quoted(query.name, sizeof query.name);
        printf(",\"type\":%u,\"min\":%d,\"max\":%d,\"step\":%d,\"default\":%d,\"flags\":%u",
               query.type, query.minimum, query.maximum, query.step, query.default_value, query.flags);
        struct v4l2_control control = {0}; control.id = query.id;
        if (query.type == V4L2_CTRL_TYPE_CTRL_CLASS || (query.flags & V4L2_CTRL_FLAG_WRITE_ONLY)) {
            printf(",\"current\":null,\"read_skipped\":true");
        } else if (!ioctl(fd, VIDIOC_G_CTRL, &control)) {
            printf(",\"current\":%d", control.value);
        } else {
            printf(",\"current\":null,\"read_errno\":%d", errno);
        }
        if ((query.type == V4L2_CTRL_TYPE_MENU || query.type == V4L2_CTRL_TYPE_INTEGER_MENU)
            && query.minimum >= 0 && query.maximum < 256) {
            printf(",\"menu\":{"); int first = 1;
            for (int index = query.minimum; index <= query.maximum; ++index) {
                struct v4l2_querymenu menu = {0}; menu.id = query.id; menu.index = (unsigned int)index;
                if (ioctl(fd, VIDIOC_QUERYMENU, &menu)) continue;
                if (!first) { putchar(','); }
                first = 0;
                printf("\"%d\":", index);
                if (query.type == V4L2_CTRL_TYPE_INTEGER_MENU) printf("%" PRId64, (int64_t)menu.value);
                else quoted(menu.name, sizeof menu.name);
            }
            putchar('}');
        }
        putchar('}'); query.id |= V4L2_CTRL_FLAG_NEXT_CTRL;
    }
    int last_error = errno;
    printf("],\"enumeration_errno\":%d,\"readonly_open\":true,\"setting_writes\":0}\n", last_error);
    close(fd);
    return count ? 0 : 6;
}
