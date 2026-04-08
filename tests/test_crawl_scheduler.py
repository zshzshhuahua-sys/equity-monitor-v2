import unittest

from src.services.crawl_scheduler_service import CrawlSchedulerService


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, **kwargs):
        self.jobs.append(kwargs)

    def get_job(self, job_id):
        for job in self.jobs:
            if job["id"] == job_id:
                class Job:
                    def __init__(self, trigger):
                        self.trigger = trigger
                        self.next_run_time = None
                return Job(job["trigger"])
        return None


class CrawlSchedulerTests(unittest.TestCase):
    def test_add_scheduled_job_registers_daily_run(self):
        service = CrawlSchedulerService()
        service.scheduler = FakeScheduler()

        service._add_scheduled_job()

        self.assertEqual(len(service.scheduler.jobs), 1)
        job = service.scheduler.jobs[0]
        self.assertEqual(job["id"], service.JOB_ID_SCHEDULED)
        self.assertEqual(str(job["trigger"]), "cron[hour='22', minute='0']")

    def test_get_schedule_status_reports_registered_job(self):
        service = CrawlSchedulerService()
        service.scheduler = FakeScheduler()
        service._is_running = True

        service._add_scheduled_job()
        status = service.get_schedule_status()

        self.assertEqual(status["job_id"], "scheduled_crawl")
        self.assertEqual(status["cron_hours"], "22")
        self.assertEqual(status["timezone"], "Asia/Shanghai")
        self.assertTrue(status["is_running"])
        self.assertTrue(status["registered"])


if __name__ == "__main__":
    unittest.main()
