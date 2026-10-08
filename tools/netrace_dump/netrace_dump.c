/*
 * netrace_dump: convert a Netrace trace (.tra.bz2) into plain text that
 * nocsim can replay.
 *
 * Build against the Netrace 1.0 reader (https://www.cs.utexas.edu/~netrace/):
 *   gcc -O2 -I<netrace-1.0> netrace_dump.c <netrace-1.0>/netrace.c \
 *       <netrace-1.0>/queue.c -o netrace_dump
 *
 * Usage: netrace_dump trace.tra.bz2 [region] [max_packets] > out.txt
 *   region:      index of the trace region to dump (default 2 = region of
 *                interest; -1 = whole trace)
 *   max_packets: stop after this many packets (default 0 = no limit)
 *
 * Output: a header block of lines starting with '#', then one line per packet:
 *   id cycle src dst size_bytes type n_deps dep1,dep2,...
 * where the dependency ids are copied verbatim from the trace (packets whose
 * injection depends on this packet being delivered, as defined by Netrace).
 */
#include <stdio.h>
#include <stdlib.h>
#include "netrace.h"

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s trace.tra.bz2 [region] [max_packets]\n", argv[0]);
        return 1;
    }
    int region = argc > 2 ? atoi(argv[2]) : 2;
    unsigned long long max_packets = argc > 3 ? strtoull(argv[3], NULL, 10) : 0;

    nt_open_trfile(argv[1]);
    /* We record each packet's dependency ids ourselves, so the reader's own
     * dependency bookkeeping (a large hash of pending packets) is unneeded
     * and makes the dump far slower. */
    nt_disable_dependencies();
    nt_header_t *h = nt_get_trheader();
    printf("# benchmark %s\n", h->benchmark_name);
    printf("# nodes %u\n", (unsigned)h->num_nodes);
    printf("# total_cycles %llu\n", h->num_cycles);
    printf("# total_packets %llu\n", h->num_packets);
    for (unsigned i = 0; i < h->num_regions; i++)
        printf("# region %u cycles %llu packets %llu\n", i,
               h->regions[i].num_cycles, h->regions[i].num_packets);

    unsigned long long limit = 0;
    if (region >= 0 && (unsigned)region < h->num_regions) {
        nt_seek_region(&h->regions[region]);
        limit = h->regions[region].num_packets;
        printf("# dumped_region %d\n", region);
    } else {
        limit = h->num_packets;
        printf("# dumped_region all\n");
    }
    if (max_packets && max_packets < limit) limit = max_packets;
    printf("# format id cycle src dst size_bytes type n_deps deps\n");

    for (unsigned long long n = 0; n < limit; n++) {
        nt_packet_t *p = nt_read_packet();
        if (!p) break;
        printf("%u %llu %u %u %d %u %u ", p->id, p->cycle, (unsigned)p->src, (unsigned)p->dst,
               nt_get_packet_size(p), (unsigned)p->type, (unsigned)p->num_deps);
        for (unsigned d = 0; d < p->num_deps; d++)
            printf(d ? ",%u" : "%u", p->deps[d]);
        printf(p->num_deps ? "\n" : "-\n");
        nt_packet_free(p);
    }
    nt_close_trfile();
    return 0;
}
