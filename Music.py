import discord
from discord.ext import commands, tasks
import asyncio
import yt_dlp
from dotenv import load_dotenv
import urllib.parse, urllib.request, re
import functools
from yt_dlp import YoutubeDL
from datetime import datetime, timedelta
from discord import app_commands
from data import PlaylistImage
import urllib.parse
from token_APP import *
def run_bot():
    load_dotenv()
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    client = commands.Bot(command_prefix="!", intents=intents)

    queues = {}
    voice_clients = {}
    last_activity = {}

    yt_dl_options = {"format": "bestaudio/best", "extract_flat": "in_playlist"}
    ytdl = yt_dlp.YoutubeDL(yt_dl_options)

    ffmpeg_options = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                      'options': '-vn -filter:a "volume=0.25"'}

    async def extract_info(loop, link):
        return await loop.run_in_executor(None, functools.partial(ytdl.extract_info, link, download=False))

    @client.event
    async def on_ready():
        try:
            synced = await client.tree.sync()
            print(f"Synced {len(synced)} command(s)")
        except Exception as e:
            print(e)
        check_inactivity.start()

    @tasks.loop(minutes=1)
    async def check_inactivity():
        for guild_id in list(voice_clients.keys()):
            voice_client = voice_clients[guild_id]
            if voice_client.is_playing():
                last_activity[guild_id] = datetime.utcnow()
            elif len(voice_client.channel.members) == 1 and voice_client.channel.members[0].id == client.user.id:
                # Déconnecter si le bot est seul dans le salon vocal
                channel = voice_client.channel
                embed = discord.Embed(title="Déconnexion",
                                      description="Le bot s'est déconnecté car il n'y avait plus personne dans le salon vocal.",
                                      color=0xFF0000)
                await channel.send(embed=embed)
                await voice_client.disconnect()
                voice_clients.pop(guild_id, None)
                queues.pop(guild_id, None)
                last_activity.pop(guild_id, None)
            elif datetime.utcnow() - last_activity.get(guild_id, datetime.utcnow()) > timedelta(minutes=5):
                # Déconnecter si aucune musique n'a été jouée pendant plus de 5 minutes
                channel = voice_client.channel
                embed = discord.Embed(title="Déconnexion",
                                      description="Le bot s'est déconnecté car aucune musique n'a été jouée pendant plus de 5 minutes.",
                                      color=0xFF0000)
                await channel.send(embed=embed)
                await voice_client.disconnect()
                voice_clients.pop(guild_id, None)
                queues.pop(guild_id, None)
                last_activity.pop(guild_id, None)

    async def play_next(interaction):
        if interaction.guild.id in queues and queues[interaction.guild.id]:
            link, title, duration, thumbnail_url, author, requester = queues[interaction.guild.id].pop(0)
            voice_client = voice_clients[interaction.guild.id]
            if voice_client and voice_client.is_connected():
                song = await get_song(link)
                player = discord.FFmpegOpusAudio(song, **ffmpeg_options)
                voice_client.play(player,
                                  after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction), client.loop))
                is_playlist = thumbnail_url == PlaylistImage
                await send_now_playing(interaction, title, duration, thumbnail_url, link, author, requester,
                                       is_playlist)
                last_activity[interaction.guild.id] = datetime.utcnow()

    async def get_song(link):
        data = await extract_info(client.loop, link)
        return data['url']

    async def send_now_playing(interaction, title, duration, thumbnail_url, link, author, requester, is_playlist=False):
        duration_str = str(timedelta(seconds=duration))
        embed = discord.Embed(title="En cours de lecture :", description=f"**[{title}]({link})**", color=0x1ABC9C)
        embed.add_field(name="Durée de la musique :", value=duration_str, inline=False)
        embed.add_field(name="Demandée par :", value=requester, inline=False)
        if author:
            author_field = author
        else:
            author_field = "Auteur inconnu"
        embed.add_field(name="Auteur :", value=author_field, inline=False)
        if is_playlist:
            embed.set_thumbnail(url=PlaylistImage)
        elif thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        await interaction.followup.send(embed=embed)

    @client.tree.command(name="play", description="Jouer une musique à partir d'un lien ou d'un terme de recherche")
    @app_commands.describe(
        link="Lien de la vidéo/playlist YouTube à jouer ou le nom d'une musique",
    )
    async def play(interaction: discord.Interaction, link: str):
        await interaction.response.defer()

        if not await ensure_voice(interaction):
            return

        if interaction.guild.id not in queues:
            queues[interaction.guild.id] = []

        if interaction.guild.id in voice_clients and voice_clients[interaction.guild.id].is_connected():
            voice_client = voice_clients[interaction.guild.id]
        else:
            voice_client = await interaction.user.voice.channel.connect()
            voice_clients[interaction.guild.id] = voice_client

        if 'list=' in link:
            await handle_youtube_playlist(interaction, link)
        elif 'youtube.com/watch?' in link:
            await handle_youtube_link(interaction, link)
        elif 'spotify' in link:
            embed = discord.Embed(title="Erreur",
                                  description="Spotify pas encore pris en charge, en cours de développement.",
                                  color=0xFF0000)
            await interaction.followup.send(embed=embed)
        else:
            await handle_youtube_search(interaction, link)

        if not voice_client.is_playing():
            await play_next(interaction)

    async def handle_youtube_playlist(interaction, link):
        try:
            data = await extract_info(interaction.client.loop, link)
            if 'entries' in data:
                for entry in data['entries']:
                    title = entry.get('title', 'Titre inconnu')
                    thumbnail_url = PlaylistImage
                    track_duration = entry.get('duration', 0)
                    author = entry.get('uploader', 'Auteur inconnu')
                    video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                    queues[interaction.guild.id].append(
                        (video_url, title, track_duration, thumbnail_url, author, interaction.user.mention))

                embed = discord.Embed(title="Playlist ajoutée",
                                      description=f"{len(data['entries'])} musiques ajoutées à la file d'attente",
                                      color=0x1ABC9C)
                await interaction.followup.send(embed=embed)
            else:
                embed = discord.Embed(title="Erreur", description="Aucune entrée n'a été trouvée dans la playlist.",
                                      color=0xFF0000)
                await interaction.followup.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(title="Erreur", description=f"Erreur lors du traitement de la playlist : {str(e)}",
                                  color=0xFF0000)
            await interaction.followup.send(embed=embed)

    async def handle_youtube_link(interaction, link):
        try:
            data = await extract_info(interaction.client.loop, link)
            song = data['url']
            track_duration = data.get('duration', 0)
            thumbnail_url = data.get('thumbnail', '')
            author = data.get('uploader', 'Auteur inconnu')
            queues[interaction.guild.id].append(
                (link, data['title'], track_duration, thumbnail_url, author, interaction.user.mention))

            embed = discord.Embed(title="Musique ajoutée à la file d'attente",
                                  description=f"**[{data['title']}]({link})**", color=0x1ABC9C)
            embed.add_field(name="Durée de la musique :", value=str(timedelta(seconds=track_duration)), inline=False)
            embed.add_field(name="Demandée par :", value=interaction.user.mention, inline=False)
            if author:
                author_field = author
            else:
                author_field = "Auteur inconnu"
            embed.add_field(name="Auteur :", value=author_field, inline=False)
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)
            await interaction.followup.send(embed=embed)
        except yt_dlp.utils.DownloadError as e:
            embed = discord.Embed(title="Erreur",
                                  description=f"Erreur de téléchargement des informations musicales : {str(e)}",
                                  color=0xFF0000)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(title="Erreur", description=f"Erreur : {str(e)}", color=0xFF0000)
            await interaction.followup.send(embed=embed)

    async def handle_youtube_search(interaction, query):
        query_string = urllib.parse.urlencode({'search_query': query})
        content = urllib.request.urlopen('https://www.youtube.com/results?' + query_string)
        search_results = re.findall(r'/watch\?v=(.{11})', content.read().decode())
        if search_results:
            link = 'https://www.youtube.com/watch?v=' + search_results[0]
            await handle_youtube_link(interaction, link)
        else:
            embed = discord.Embed(title="Erreur", description="Aucun résultat n'a été trouvé pour votre requête.",
                                  color=0xFF0000)
            await interaction.followup.send(embed=embed)

    async def ensure_voice(interaction):
        if not interaction.user.voice:
            embed = discord.Embed(title="Erreur",
                                  description="Tu dois être connecté à un salon vocal pour utiliser cette commande.",
                                  color=0xFF0000)
            await interaction.followup.send(embed=embed)
            return False
        return True

    @client.tree.command(name="file_d_attente", description="Afficher la file d'attente")
    async def viewqueue(interaction: discord.Interaction):
        try:
            await interaction.response.defer()  # Déférer la réponse pour indiquer que le bot travaille

            if interaction.guild.id in queues and queues[interaction.guild.id]:
                total_duration = sum(entry[2] for entry in queues[interaction.guild.id])
                current_track = queues[interaction.guild.id][0]
                embed = discord.Embed(title="Informations sur la file d'attente :", color=0x00ff00)
                embed.add_field(name="En cours de lecture :", value=f"**{current_track[1]}**", inline=False)
                embed.add_field(name="Durée de la musique :",
                                value=str(timedelta(seconds=current_track[2])),
                                inline=False)
                embed.add_field(name="Nombre de musiques dans la file d'attente",
                                value=str(len(queues[interaction.guild.id])), inline=False)
                embed.add_field(name="Durée totale de la file d'attente",
                                value=str(timedelta(seconds=total_duration)),
                                inline=False)
            else:
                embed = discord.Embed(title="File d'attente actuelle", description="La file d'attente est vide !",
                                      color=0xff0000)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            print(f"Exception occurred: {str(e)}")

    @client.tree.command(name="vider_file_d_attente", description="Vider la file d'attente")
    async def clear_queue(interaction: discord.Interaction):
        await interaction.response.defer()  # Déférer la réponse pour indiquer que le bot travaille

        if interaction.guild.id in queues:
            queues[interaction.guild.id].clear()
            embed = discord.Embed(title="File d'attente vidée", description="La file d'attente a été vidée.",
                                  color=0x1ABC9C)
        else:
            embed = discord.Embed(title="Erreur", description="La file d'attente est déjà vide.", color=0xFF0000)

        await interaction.followup.send(embed=embed)

    @client.tree.command(name="pause", description="Mettre la lecture en pause")
    async def pause(interaction: discord.Interaction):
        if interaction.guild.id in voice_clients:
            voice_clients[interaction.guild.id].pause()
            embed = discord.Embed(title="Lecture en pause", description="La lecture a été mise en pause.",
                                  color=0x1ABC9C)
            await interaction.response.send_message(embed=embed)

    @client.tree.command(name="resume", description="Reprendre la lecture en cours")
    async def resume(interaction: discord.Interaction):
        if interaction.guild.id in voice_clients:
            voice_clients[interaction.guild.id].resume()
            embed = discord.Embed(title="Lecture reprise", description="La lecture a été reprise.", color=0x1ABC9C)
            await interaction.response.send_message(embed=embed)

    @client.tree.command(name="disconnect", description="Arrêter la lecture et se déconnecter")
    async def stop(interaction: discord.Interaction):
        if interaction.guild.id in voice_clients:
            voice_clients[interaction.guild.id].stop()
            try:
                await voice_clients[interaction.guild.id].disconnect()
            except Exception as e:
                embed = discord.Embed(title="Erreur", description=f"Erreur lors de la déconnexion : {str(e)}",
                                      color=0xFF0000)
                await interaction.response.send_message(embed=embed)
            else:
                del voice_clients[interaction.guild.id]
                queues[interaction.guild.id].clear()
                embed = discord.Embed(title="Lecture arrêtée", description="Lecture arrêtée et déconnectée.",
                                      color=0x1ABC9C)
                await interaction.response.send_message(embed=embed)

    @client.tree.command(name="skip", description="Passer la musique actuelle")
    async def skip(interaction: discord.Interaction):
        await interaction.response.defer()  # Déférer la réponse pour indiquer que le bot travaille

        if not await ensure_voice(interaction):
            return

        guild_id = interaction.guild.id
        voice_client = voice_clients.get(guild_id)

        if voice_client and voice_client.is_connected():
            voice_client.stop()

            embed = discord.Embed(title="Musique passée",
                                  description=f"Musique passée par : {interaction.user.mention}",
                                  color=0x1ABC9C)
            await interaction.followup.send(embed=embed)

            if queues[guild_id]:
                await play_next(interaction)
            else:
                embed = discord.Embed(title="Fin de la file d'attente",
                                      description="Il n'y a plus de musiques dans la file d'attente. Lecture arrêtée.",
                                      color=0x1ABC9C)
                await interaction.followup.send(embed=embed)
        else:
            if interaction.user.voice:
                voice_client = await interaction.user.voice.channel.connect()
                voice_clients[guild_id] = voice_client
                if queues[guild_id]:
                    await play_next(interaction)
                else:
                    embed = discord.Embed(title="Fin de la file d'attente",
                                          description="Il n'y a plus de musiques dans la file d'attente.",
                                          color=0x1ABC9C)
                    await interaction.followup.send(embed=embed)
            else:
                embed = discord.Embed(title="Erreur",
                                      description="Tu dois être dans un salon vocal pour passer des musiques.",
                                      color=0xFF0000)
                await interaction.followup.send(embed=embed)

    client.run(token_Aria_music_bot)

run_bot()