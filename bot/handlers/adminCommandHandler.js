import {bot} from "../app.js";
import log from "../logging/logging.js";
import groupService from "../services/groupService.js";
import userService from "../services/userService.js";
import ScheduleController from "../controllers/ScheduleController.js";
import fs from "fs/promises"
import UserActivityService from "../services/userActivityService.js";
import userActivityService from "../services/userActivityService.js";
import userRegistrationStatService from "../services/userRegistrationStatService.js";
import {isAdminMiddleware} from "../middlewares/bot/isAdminMiddleware.js";
import {updateFacultiesCommandController} from "../controllers/commands/adminCommands/updateFaculties.js";
import {updateProgramsCommandController} from "../controllers/commands/adminCommands/updatePrograms.js";
import {updateGroupsCommandController} from "../controllers/commands/adminCommands/updateGroups.js";
import {updateProfilesCommandController} from "../controllers/commands/adminCommands/updateProfiles.js";
import blackListService from "../services/blackListService.js";
import {updateDepartmentsCommandController} from "../controllers/commands/adminCommands/updateDepartments.js";
import {updateTeachersCommandController} from "../controllers/commands/adminCommands/updateTeachers.js";
import {updateSchedulesCommandController} from "../controllers/commands/adminCommands/updateSchedules.js";
import {getUserCommandController, getUserLogsCommandController} from "../controllers/commands/adminCommands/getUser.js";
import {syncNewDataController} from "../controllers/commands/adminCommands/syncNewData.js";
import config from "../config.js";
import { archiveAndBackupLogs } from "../cron/logArchiver.js";
import { testTargetChannel } from "../services/mediaCollectorService.js";
import { handleBanCommand, handleMuteCommand, handleUnbanCommand, handleBansListCommand } from "../controllers/commands/adminCommands/moderationCommands.js";
import { handlePaywallCommand, handleUnpaywallCommand, handlePaywallsListCommand } from "../controllers/commands/adminCommands/paywallCommands.js";


export function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}


export default function setupAdminCommandHandler() {
  bot.onText(/^\/(sync|pull_new)/i, async (msg) => {
    await isAdminMiddleware(msg, async () => {
      await syncNewDataController(msg);
    });
  });

  bot.onText(/^\/updateFaculties/, async (msg) => {
    await isAdminMiddleware(msg, async () => {
      let hard = false
      if (msg.text.includes("hard")) {
        hard = true
      }
      await updateFacultiesCommandController(hard)
    })
  })

  bot.onText(/^\/updatePrograms/, async (msg) => {
    await isAdminMiddleware(msg, async () => {
      let hard = false
      if (msg.text.includes("hard")) {
        hard = true
      }
      await updateProgramsCommandController(hard)
    })
  })

  bot.onText(/^\/updateGroups/, async (msg) => {
    await isAdminMiddleware(msg, async () => {
      let hard = false
      if (msg.text.includes("hard")) {
        hard = true
      }
      await updateGroupsCommandController(hard)
    })
  })

  bot.onText(/^\/updateProfiles/, async (msg) => {
    await isAdminMiddleware(msg, async () => {
      let hard = false
      if (msg.text.includes("hard")) {
        hard = true
      }
      await updateProfilesCommandController(hard)
    })
  })

  bot.onText(/^\/updateDepartments/, async (msg) => {
    await isAdminMiddleware(msg, async () => {
      let hard = false
      if (msg.text.includes("hard")) {
        hard = true
      }
      await updateDepartmentsCommandController(hard)
    })
  })

  bot.onText(/^\/updateTeachers/, async (msg) => {
    await isAdminMiddleware(msg, async () => {
      let hard = false
      if (msg.text.includes("hard")) {
        hard = true
      }
      await updateTeachersCommandController(hard)
    })
  })

  bot.onText(/^\/updateSchedules/i, async (msg) => {
    await isAdminMiddleware(msg, async () => {
      let hard = false
      if (msg.text.includes("hard")) {
        hard = true
      }
      await updateSchedulesCommandController(hard)
    })
  })


  bot.onText(/^\/info$/, async (msg) => {
    if (!await userService.isAdmin(msg.from.id)) {
      return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
    }
    log.warn("Тестовый лог!")
    await bot.sendMessage(msg.chat.id, JSON.stringify(msg, null, 2))
  })

  bot.onText(/^\/test$/, async (msg) => {
    if (!await userService.isAdmin(msg.from.id)) {
      return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
    }
    try {
      await axios.get(config.KSU_DOMAIN)
    } catch (e) {
      console.log(e)
      log.error("Ошибочка в /test", {stack: e.stack})
    }
  })

  bot.onText(/^\/stat(?:@\w+)?(?:\s|$)/i, async (msg) => {
    if (!await userService.isAdmin(msg.from.id)) {
      return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
    }
    try {
      const activeUsersToday = await userActivityService.getTodayUserCount()
      const registeredUsersToday = await userRegistrationStatService.getTodayRegisteredUserCount()

      let msg_text = 'Статистика ежедневного онлайна (время Казахстана UTC+5): \n' +
        `Сегодня: ${activeUsersToday}\n`
      const {weakUserActivity, weakUserCount} = await UserActivityService.getWeakUserActivity()
      for (const doc of weakUserActivity) {
        const date = new Date(doc.createdAt);
        const kzDate = new Date(date.getTime() + 5 * 60 * 60 * 1000);
        const day = kzDate.getUTCDate().toString().padStart(2, '0');
        const month = (kzDate.getUTCMonth() + 1).toString().padStart(2, '0');

        msg_text += `${day}.${month}: ${doc.userActivity}\n`
      }
      msg_text += `Всего уникальных пользователей за неделю: ${weakUserCount}\n\n`

      msg_text += 'Статистика ежедневных регистраций (время Казахстана UTC+5): \n' +
        `Сегодня: ${registeredUsersToday}\n`
      const {
        weakRegisteredUserCount,
        weakRegistrationStat
      } = await userRegistrationStatService.getWeakRegistrationStat()
      for (const doc of weakRegistrationStat) {
        const date = new Date(doc.createdAt);
        const kzDate = new Date(date.getTime() + 5 * 60 * 60 * 1000);
        const day = kzDate.getUTCDate().toString().padStart(2, '0');
        const month = (kzDate.getUTCMonth() + 1).toString().padStart(2, '0');

        msg_text += `${day}.${month}: ${doc.registeredUsers}\n`
      }
      msg_text += `Всего зареганных пользователей за неделю: ${weakRegisteredUserCount}`

      await bot.sendMessage(msg.chat.id, msg_text)
    } catch (e) {
      log.error("Ошибочка в /stat", {stack: e.stack})
    }
  })

  bot.onText(/^\/users(?:@\w+)?$/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }
      const userCount = await userService.countDocuments()
      await bot.sendMessage(msg.chat.id, `В базе данных Кичо ${userCount} пользователей.`)
    } catch (e) {
      log.error({stack: e.stack})
    }
  })

  bot.onText(/^\/group_stat(?:@\w+)?$/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }
      await bot.sendMessage(msg.chat.id, "started")
      const startTime = Date.now()

      let group_stat = []

      const groups = await groupService.getAll()
      const users = await userService.getAll()

      for (const group of groups) {
        const our_users = users.filter(user => user.group === group.id)
        group_stat.push({
          id: group.id,
          name: group.name,
          students: group.studentCount,
          our_users: our_users.length,
          not_our_users: group.studentCount - our_users.length,
          ratio: our_users.length / group.studentCount
        })
      }

      const sortedGroupStat = group_stat.slice().sort((a, b) => b.not_our_users - a.not_our_users);

      const activeGroups = sortedGroupStat.filter(group => group.ratio > 0).slice(0, 20)

      let msg_text = 'Потенциальные юзеры: \n'
      for (const group of activeGroups) {
        msg_text += `ID: ${group.id}, All: ${group.students} | Не наши: ${group.not_our_users}\n` +
          `/get_users_by_group${group.id}\n`
      }

      await fs.writeFile("./temp/group_stat.json", JSON.stringify(sortedGroupStat, null, 3))
      const endTime = Date.now()
      await bot.sendDocument(msg.chat.id, './temp/group_stat.json', {caption: `Action time: ${(endTime - startTime) / 1000} сек.\n}`})
      await bot.sendMessage(msg.chat.id, msg_text)
    } catch (e) {
      console.log(e)
      log.error("error", {stack: e.stack})
    }
  })

  bot.onText(/^\/get_schedule(?:@\w+)?(?:\s+(\w+)|\s*$)/i, async (msg, match) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }
      const groupId = match[1];
      if (!groupId) {
        return await bot.sendMessage(msg.chat.id, "ℹ️ Использование: <code>/get_schedule [ID группы]</code>", { parse_mode: "HTML" });
      }

      const answer = await bot.sendMessage(msg.chat.id, `Получаем расписание группы: ${groupId}`)
      const Group = await groupService.getById(groupId)
      if (!Group) {
        return bot.editMessageText("Нет такой группы. ", {
          chat_id: answer.chat.id, message_id: answer.message_id
        })
      }

      const call = {
        data: `schedule|${Group.language}|${groupId}|${ScheduleController.getCurrentDayNumber()}`,
        message: answer
      }
      await ScheduleController.getScheduleMenu(call)
    } catch (e) {
      log.error({stack: e.stack})
    }

  });

  bot.onText(/^\/get_reserved_schedule(?:@\w+)?(?:\s+(\w+)|\s*$)/i, async (msg, match) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }
      const groupId = match[1];
      if (!groupId) {
        return await bot.sendMessage(msg.chat.id, "ℹ️ Использование: <code>/get_reserved_schedule [ID группы]</code>", { parse_mode: "HTML" });
      }

      const answer = await bot.sendMessage(msg.chat.id, `Получаем расписание группы: ${groupId}`)
      const Group = await groupService.getById(groupId)
      if (!Group) {
        return bot.editMessageText("Нет такой группы. ", {
          chat_id: answer.chat.id, message_id: answer.message_id
        })
      }

      const call = {
        data: `schedule|${Group.language}|${groupId}|${ScheduleController.getCurrentDayNumber()}`,
        message: answer
      }
      await ScheduleController.getReservedSchedule(call, groupId)
    } catch (e) {
      log.error({stack: e.stack})
    }

  });

  bot.onText(/^\/(?:get_user|find_user|user)(?:@\w+)?/i, getUserCommandController);
  bot.onText(/^\/user_logs(?:@\w+)?/i, getUserLogsCommandController);

  bot.onText(/^\/ignoreLogs(?:@\w+)?/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }
      const rawArg = msg.text.replace(/^\/ignoreLogs(?:@\w+)?(?:\s+|_)?/i, '').trim();
      const userId = parseFloat(rawArg)
      if (isNaN(userId)) {
        return await bot.sendMessage(msg.chat.id, "ℹ️ Использование: <code>/ignoreLogs [userId]</code>", { parse_mode: "HTML" })
      }

      await blackListService.addToBlackList(userId)

      await bot.sendMessage(msg.chat.id, `User ${userId} добавлен в чёрный список логов`)
    } catch (e) {
      log.error("Ошибочка при /ignoreLogs", {stack: e.stack})
    }
  });

  bot.onText(/^\/(?:backup_logs|archive_logs|export_logs)(?:@\w+)?(?:\s|$)/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!");
      }
      const statusMsg = await bot.sendMessage(msg.chat.id, "⏳ Начинаю архивацию логов и выгрузку на Google Диск / Telegram...");
      await archiveAndBackupLogs();
      await bot.editMessageText("✅ Архивация завершена! Логи выгружены в облако.", {
        chat_id: statusMsg.chat.id,
        message_id: statusMsg.message_id
      });
    } catch (e) {
      log.error("Ошибка при /backup_logs: " + e.message, { stack: e.stack });
      await bot.sendMessage(msg.chat.id, "❌ Ошибка при архивации: " + e.message);
    }
  });


  bot.onText(/^\/get_users_by_group(?:@\w+)?/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }
      const rawArg = msg.text.replace(/^\/get_users_by_group(?:@\w+)?(?:\s+|_)?/i, '').trim();
      const groupId = parseFloat(rawArg)
      if (isNaN(groupId)) {
        return await bot.sendMessage(msg.chat.id, "ℹ️ Использование: <code>/get_users_by_group [ID группы]</code>", { parse_mode: "HTML" })
      }

      const users = await userService.getUsersByGroupId(groupId)

      let msg_text = `Group ${groupId} users (${users.length}): \n`
      for (const user of users) {
        msg_text += `/get_user${user.userId}\n`
      }

      await bot.sendMessage(msg.chat.id, msg_text)
    } catch (e) {
      log.error("Ошибочка при /get_users_by_group", {stack: e.stack})
    }

  });


  bot.onText(/^\/get_logs/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }

      await bot.sendDocument(msg.chat.id, './logs.log')
    } catch (e) {
      log.error("Ошибочка при /get_logs", {stack: e.stack})
    }

  });

  bot.onText(/^\/sms(?:@\w+)?(?:\s|$)/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "⛔ У вас нет доступа к этой команде!");
      }
      const rawText = msg.text.replace(/^\/sms(?:@\w+)?\s*/i, '').trim();
      const split_data = rawText.split(/\s+/).filter(Boolean);
      if (split_data.length < 2 || !rawText) {
        return await bot.sendMessage(
          msg.chat.id,
          "ℹ️ <b>Формат команды:</b>\n" +
          "• <code>/sms [userId или @username] [текст]</code> — отправить одно личное сообщение\n" +
          "• <code>/sms all [текст]</code> — разовое оповещение всем пользователям\n\n" +
          "Примеры:\n" +
          "• <code>/sms @username Привет, обнови расписание!</code>\n" +
          "• <code>/sms 123456789 Привет!</code>\n" +
          "• <code>/sms all Важное техническое объявление!</code>",
          { parse_mode: "HTML" }
        );
      }
      const target = split_data[0];
      const msg_text = rawText.slice(target.length).trim();

      // Разовое оповещение всем пользователям (без спам-циклов)
      if (target.toLowerCase() === 'all') {
        const users = await userService.getAll();
        await bot.sendMessage(msg.chat.id, `📢 Начинаю разовое оповещение ${users.length} пользователей...`);
        let sentCount = 0;
        let failCount = 0;
        for (const user of users) {
          try {
            await bot.sendMessage(user.userId, msg_text, { disable_web_page_preview: true });
            sentCount++;
          } catch (e) {
            failCount++;
          }
          await sleep(500); // бережная пауза против лимитов Telegram API
        }
        return await bot.sendMessage(
          msg.chat.id,
          `✅ <b>Разовое оповещение завершено:</b>\n• Доставлено: ${sentCount}\n• Не доставлено (бот заблокирован): ${failCount}`,
          { parse_mode: "HTML" }
        );
      }

      // Одиночное сообщение конкретному пользователю
      const targetUser = await userService.findUser(target);
      if (!targetUser) {
        return await bot.sendMessage(msg.chat.id, `❌ Пользователь <b>"${target}"</b> не найден в базе данных бота.`, { parse_mode: "HTML" });
      }

      await bot.sendMessage(targetUser.userId, msg_text);

      const usernameStr = targetUser.username ? `@${targetUser.username}` : (targetUser.firstName || 'Без имени');
      await bot.sendMessage(
        msg.chat.id,
        `✅ <b>Сообщение успешно доставлено:</b>\n` +
        `• <b>Получатель:</b> ${usernameStr} (ID: <code>${targetUser.userId}</code>)\n` +
        `• <b>Текст:</b>\n${msg_text}`,
        { parse_mode: "HTML" }
      );
    } catch (e) {
      log.error("Ошибочка при /sms: " + e.message, { stack: e.stack });
      await bot.sendMessage(msg.chat.id, `❌ Не удалось отправить сообщение: ${e.message}`);
    }
  });

  bot.onText(/^\/get_group(?:@\w+)?(?:\s+(\w+)|\s*$)/i, async (msg, match) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }
      const groupId = match[1];
      if (!groupId) {
        return await bot.sendMessage(msg.chat.id, "ℹ️ Использование: <code>/get_group [groupId]</code>", { parse_mode: "HTML" });
      }

      const group = await groupService.getById(groupId)
      if (!group) {
        return await bot.sendMessage(msg.chat.id, `❌ Группа с ID "${groupId}" не найдена в базе данных.`);
      }
      await bot.sendMessage(msg.chat.id, JSON.stringify(group, null, 4))
    } catch (e) {
      log.error({stack: e.stack})
    }
  });

  bot.onText(/^\/restart(?:@\w+)?(?:\s|$)/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }

      await bot.sendMessage(msg.chat.id, "Браузер и Puppeteer отключены. Бот работает на официальном REST API КарУ (BuketovApiService) 🚀");
    } catch (e) {
      log.error({stack: e.stack})
    }
  });

  bot.onText(/^\/get_callback(?:@\w+)?(?:\s+(.+)|$)/i, async (msg, match) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return await bot.sendMessage(msg.chat.id, "У вас нет доступа к этой прекрасной команде!")
      }
      const callback_data = match[1]?.trim();
      if (!callback_data) {
        return await bot.sendMessage(msg.chat.id, "ℹ️ Использование: <code>/get_callback [callback_data]</code>", { parse_mode: "HTML" });
      }
      await bot.sendMessage(msg.chat.id, "here is your callback: " + callback_data, {
        reply_markup: {inline_keyboard: [[{text: callback_data, callback_data}]]}
      })
    } catch (e) {
      log.error({stack: e.stack})
    }
  })

  bot.onText(/^\/test_media(?:@\w+)?(?:\s|$)/i, async (msg) => {
    try {
      if (!await userService.isAdmin(msg.from.id)) {
        return; // Полный тихий игнор: обычные пользователи даже не узнают о существовании команды
      }
      await bot.sendMessage(msg.chat.id, "🔍 Проверяю отправку тестового сообщения в целевой медиа-канал...");
      const resMedia = await testTargetChannel(bot);

      let text = "📊 <b>Результаты проверки медиа-канала:</b>\n\n";
      text += `📁 <b>Канал «файлы от бота»</b>: <code>${resMedia.targetId}</code>\n`;
      text += resMedia.success
        ? "✅ <i>Успешно подключен! Бот может публиковать файлы из личных сообщений.</i>\n"
        : `❌ <i>Ошибка: ${resMedia.error}</i>\n`;

      await bot.sendMessage(msg.chat.id, text, { parse_mode: 'HTML' });
    } catch (e) {
      await bot.sendMessage(msg.chat.id, `Ошибка: ${e.message}`);
    }
  });

  // === Модерация (бан и мут) ===
  bot.onText(/^\/ban(?:@\w+)?(\s|$)/i, handleBanCommand);
  bot.onText(/^\/mute(?:@\w+)?(\s|$)/i, handleMuteCommand);
  bot.onText(/^\/unban(?:@\w+)?(\s|$)/i, handleUnbanCommand);
  bot.onText(/^\/unmute(?:@\w+)?(\s|$)/i, handleUnbanCommand);
  bot.onText(/^\/bans(?:@\w+)?$/i, handleBansListCommand);

  // === Платный режим кнопок (любое количество ⭐ звёзд) ===
  bot.onText(/^\/(?:paywall|toll|starwall|stars|tax)(?:@\w+)?(\s|$)/i, handlePaywallCommand);
  bot.onText(/^\/(?:unpaywall|untoll)(?:@\w+)?(\s|$)/i, handleUnpaywallCommand);
  bot.onText(/^\/(?:paywalls|tolls)(?:@\w+)?$/i, handlePaywallsListCommand);

  bot.onText(/^\/ahelp(?:@\w+)?(?:\s|$)/i, async (msg) => {
    const msg_text = '⚡ <b>КОМАНДЫ АДМИНИСТРАТОРА КИЧО:</b>\n\n' +
      '🔄 <b>Синхронизация данных:</b>\n' +
      '/sync (или /pull_new) — <i>Умный сбор новых данных: проверяет сайт КарУ и добавляет ТОЛЬКО новые группы, преподавателей, кафедры и программы БЕЗ дубликатов!</i>\n' +
      '/updateFaculties [hard] — обновить факультеты\n' +
      '/updatePrograms [hard] — обновить программы\n' +
      '/updateGroups [hard] — обновить группы (быстрый)\n' +
      '/updateDepartments [hard] — обновить кафедры\n' +
      '/updateTeachers [hard] — обновить преподавателей\n' +
      '/updateProfiles [hard] — обновить профили\n\n' +
      '⛔ <b>Модерация (бан и мут):</b>\n' +
      '/ban [ID/@ник] [время/mute] [причина] — <i>бан или мут (напр. /ban @user 2h или /ban @user mute 30m)</i>\n' +
      '/mute [ID/@ник] [время] [причина] — <i>быстрый мут пользователя (напр. /mute @user 1h)</i>\n' +
      '/unban [ID/@ник] — <i>снять бан или мут</i>\n' +
      '/bans — <i>список активных блокировок и мутов</i>\n\n' +
      '⭐ <b>Платный режим кнопок (любое число звёзд):</b>\n' +
      '/paywall [ID/@ник] [кол-во/off] — <i>сделать ВСЕ кнопки платными (напр. /paywall @user 50 или 1)</i>\n' +
      '/unpaywall [ID/@ник] — <i>выключить платный режим для пользователя</i>\n' +
      '/paywalls — <i>список пользователей на платном тарифе</i>\n\n' +
      '📊 <b>Статистика и пользователи:</b>\n' +
      '/get_user [ID/@ник] — <i>карточка пользователя с историей логов на русском!</i>\n' +
      '/user_logs [ID/@ник] [кол-во] — <i>подробные логи действий пользователя</i>\n' +
      '/find_user [@ник] — <i>быстрый поиск юзера по Telegram-нику</i>\n' +
      '/stat — статистика онлайна и регистраций\n' +
      '/users — общее количество пользователей бота\n' +
      '/group_stat — покрытие по группам (+ выгрузка JSON)\n' +
      '/get_group [groupId] — инфо о группе\n' +
      '/get_users_by_group [groupId] — пользователи группы\n' +
      '/get_schedule [groupId] — расписание группы\n' +
      '/get_reserved_schedule [groupId] — резервное расписание\n\n' +
      '📢 <b>Связь и оповещения:</b>\n' +
      '/sms [ID/@ник] [текст] — <i>отправить личное сообщение пользователю</i>\n' +
      '/sms all [текст] — <i>разовое оповещение всем пользователям (1 раз)</i>\n\n' +
      '🛠 <b>Сервисные команды:</b>\n' +
      '/backup_logs — архивировать и выгрузить логи на Google Диск / Telegram\n' +
      '/get_logs — скачать текущий лог-файл бота\n' +
      '/ignoreLogs [userId] — добавить в игнор логов\n' +
      '/restart — перезапуск сессии браузера\n' +
      '/info — отладочная информация\n' +
      '/test — тест подключения к КарУ\n' +
      '/get_callback [callbackName] — тест кнопки'
    await bot.sendMessage(msg.chat.id, msg_text, { parse_mode: "HTML" })
  });
}